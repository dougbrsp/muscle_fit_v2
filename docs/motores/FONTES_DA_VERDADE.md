# Fontes da verdade dos motores (M76)

> Recuperação, progressão, recomendação, fichas/Modelo e volume.
> Criado no **M76 Lote 0** (2026-10-06). Guarda automática:
> [`api/tests/unit/test_guarda_fontes_da_verdade.py`](../../api/tests/unit/test_guarda_fontes_da_verdade.py).

Este documento **estende** o §4 "Source of Truth Map" de
[`docs/dashboard/PLANO_NOVO_DASHBOARD_20260922.md`](../dashboard/PLANO_NOVO_DASHBOARD_20260922.md#4-source-of-truth-map).
O §4 responde "de qual **tabela** vem o dado" (catálogo, contribuições, séries, sessões,
plano, Modelo, metas, progressão, recuperação...). Este responde "qual **módulo** pode
**calcular** a regra em cima desse dado" — e onde ela está copiada hoje. Onde o §4 já
declara o dono, a linha abaixo aponta para ele em vez de repetir.

---

## 1. Princípios (valem para todo PR que mexe em motor)

1. **Um dono por regra.** Python puro, sem I/O, com **constantes nomeadas** (padrão
   existente: `intelligence/dashboard/metrics_registry.py`, `domain/training_safety.py`,
   `set_types.py`, `intelligence/dashboard/comparability.py`).
2. **Consumidor não recalcula.** Telas, SQL, lambdas, relatórios e prompts recebem o
   valor/status pronto do dono.
3. **Frontend sem regra numérica.** Onde um espelho for inevitável (offline/perf), ele é
   gerado ou coberto por **teste de paridade** contra fixture JSON exportada pelo backend
   (modelo: `template-time-estimate.parity.json`).
4. **Prompt e RAG sem número escrito à mão.** Toda faixa, limiar ou fator no texto do
   prompt é **interpolado** da fonte. Markdown de `api/privado/rag/markdown` (submódulo privado, §73) injetado em prompt conta
   como prompt; o template de prompt de `api/privado/prompts/` (M103 P1–P4, §73.1–73.4) também: número só por `{{placeholder}}`
   da tabela da feature (código público), que lê o dono.
5. **Código morto com regra paralela é apagado**, não corrigido.
6. **Guarda ratchet no CI.** Padrões proibidos fora do dono, com allowlist do estado atual
   que **só encolhe** (ver §5).
7. Mudança de **semântica** (não só de dono) vem com "Impacto em produção" medido em dados
   reais (leitura), como no M74.

## 2. Decisões do usuário (2026-10-06) — valem para todos os lotes

1. **Dose reduzida**: `ceil(S × 0,75)`, mínimo 1, **em todo lugar**. `_RECOMMENDATIONS`
   passa a expressar isso (abandona "1–2 séries"); Modo B passa a **reduzir** séries (não só
   avisar); Modelos por IA e Modo A seguem a mesma função.
2. **Sinais de desempenho na projeção**: iguais ao dashboard — persistem **até a próxima
   sessão do grupo**. O reset "≥72h à frente" de `recovery_projection.py` sai.
3. **Recorde**: a definição é a de `intelligence/dashboard/session_records.py` — carga
   direcional estritamente maior na mesma chave exercício+equipamento+convenção; 1ª
   exposição não é recorde; sem equipamento resolvido → sem recorde; reps da série recorde
   só como informação.
4. **Caminho legado de geração** (`GENERATION_ENGINE=legacy`): removido depois de 30 dias
   com `generation.fallback = 0` nos logs de `musclefit-plan-generator` e
   `musclefit-api-core`. A rede de segurança passa a ser "falhar com erro claro".

Decisão anterior que **não** é divergência: Desempenho por grupo usa RIR 0 quando a série
não tem RPE ("meio-termo", 2026-10-06). Só a cópia em SQL e os textos de UI que dizem o
contrário precisam de ajuste (Lote 3).

## 3. Mapa: regra → dono → consumidores → proibido fora do dono

Legenda dos IDs: inventário do M76 (seções A–E do §4 deste doc). "Lote" = PR(s) que
resolvem. "Guarda" = chave do padrão em `test_guarda_fontes_da_verdade.py`.

### 3.1 Transversais

| Regra | Dono (hoje → alvo) | Consumidores permitidos | Proibido fora do dono | Divergências | Lote | Guarda |
|---|---|---|---|---|---|---|
| e1RM Epley + RIR, confiável ≤ 12 reps | `domain/e1rm.py` (`estimated_1rm`, `reliable_e1rm`, `epley_multiplier`, `E1RM_MAX_RELIABLE_REPS`, `MissingRpePolicy` `SKIP` padrão / `RIR_ZERO` só Desempenho por grupo); `MuscleRecoveryService.estimated_1rm` e `THRESHOLDS["e1rm_max_reliable_reps"]` só delegam/reexportam | `comparability`, `exposure_service`, `exercise_performance`, `muscle_group_performance` (via `analytics._fetch_group_e1rm_rows`), `volume_service`, dashboards, relatórios | `(reps + rir) / 30` em Python/SQL/TS; e1RM sem teto em recorde | ✅ A resolvido no Lote 3a | 3a | `epley` (allowlist vazia) |
| RIR = 10 − RPE; defaults sem RPE | `domain/e1rm.py` (`rir_from_rpe`, `resolve_rir`, `dose_inputs`, `DOSE_REPS_WHEN_MISSING`, `DOSE_RPE_WHEN_MISSING`); `MuscleRecoveryService.estimate_rir` só delega | idem; SQL de volume por split interpola `DOSE_RPE_WHEN_MISSING` | `10 - rpe` em qualquer lugar; reps 8 / RPE 7,0 soltos | ✅ A resolvido no Lote 3a | 3a | `rir`, `dose_sem_rpe` (allowlists vazias) |
| Série de trabalho / preparatória (inclui `laboral`, M100 §72) / aplicação de técnica | `set_types.py` (§4 "Séries"): vocabulário, apelidos, `sql_not_preparatory()` (com apelidos), `sql_canonical_set_type_case()`, `is_linked_application()` | todo agregado via `sql_not_preparatory()`/`is_working_set()`; API devolve `set_type` canônico; FE via **um** `frontend/src/lib/set-types.ts` lendo `set-types.generated.json` (paridade) | `CASE` próprio de `set_type`; apelido `'dropset'` à mão; `set_type === 'working'`; `backoff` como working no FE | ✅ A resolvido no Lote 3b | 3b | `set_type_paralelo` (allowlist vazia) |
| `trained_at = COALESCE(started_at, ended_at, created_at → fuso do usuário)` | `intelligence/dashboard/time_windows.py`: `trained_at_sql(alias, offset_minutes)` e `with_trained_at(sql, fuso)` (marcador `{trained_at}`) | todo SQL de motor | `COALESCE(a.started_at, a.ended_at, …)` escrito à mão; `created_at` (UTC) sem conversão | ✅ A, B10 resolvidos no Lote 2b | 2b | `trained_at` (allowlist vazia) |
| Pesos por papel (PRIMARY/SECONDARY/STABILIZER) | **leitura**: `stimulus_weight` da tabela `exercise_muscle_contributions` (§4 "Contribuição"), resolvido por `VolumeService._fetch_contributions_by_mapping` + `_resolve_contributions`; **escrita**: `muscle_contribution_resolver.DEFAULT_WRITE_WEIGHTS` (1,0/0,5/0,25), servida ao admin FE por `motor-constants.generated.json`; **sem contribuição** → PRIMARY em "Outros" (política única); **leitura de `fatigue_weight`**: só o indicador de fadiga do grupo do Modo Treino (M79, §3.6), via `prior_fatigue.fatigue_weight_of`, com fallback no `stimulus_weight` da mesma linha (coluna `NOT NULL`: 0 nulos em 352 contribuições ativas em 2026-10-11) | `VolumeService`, `_resolve_muscle_contributions_for_mappings`, tela Tendência (`muscle_groups_mapped.aggregate`), catálogo legado (`exercise_mapping_catalog`), admin FE (`muscle-contribution-weights.ts`) | 0,5/0,3/0,25 de SECONDARY escrito à mão; `SECONDARY_FACTOR`; fator fixo na leitura | ✅ A resolvido no Lote 3c | 3c | `peso_secundario` (allowlist vazia) |
| Recorde pessoal | `intelligence/dashboard/session_records.py` (decisão 3): `record_events`, `current_record`, `key_for`, `record_set_reps`; "Melhor carga" sem recorde: `best_load`, `current_mark` (Lote 3d) | dashboard (`adherence_service`), Modo Treino (`domain/equipment_record.current_mark` → `GET /exercises/last-session-sets`, `record.kind`), recordes do relatório (`progression_service.get_recent_records` → `recent_records`) | critério próprio de recorde (`MAX(best_weight_kg)` por nome, desempate por reps, 1ª exposição como recorde, equipamentos misturados) | ✅ A resolvido no Lote 3b | 3b | `recorde_paralelo` (allowlist vazia) |
| Progresso / regressão / platô por exercício | `intelligence/dashboard/comparability.py` + `exposure_service.py` (M74, [regra única](../dashboard/PLANO_NOVO_DASHBOARD_20260922.md#8-regras-de-comparabilidade-e-qualidade)); desde o PR 2 da fadiga prévia (§22) a chave inclui `fatigue_context` (faixa do dono `prior_fatigue` sobre a sessão real, `session_fatigue_reader`) e o run comparável é o do contexto da última exposição (`trailing_comparable_indices`: outro contexto é pulado, troca física interrompe); recorde/e1RM continuam absolutos (chave física); desde o M78 (2026-10-11) as reps emparelhadas na mesma carga de topo valem `reps + RIR` por série (`WorkingSet.effort_reps`, RIR de `domain/e1rm.rir_from_rpe`) quando todas as 2N séries emparelhadas têm RPE, senão reps cruas nos dois lados (`paired_top_reps` devolve `PairedTopReps` com `effort_adjusted`); `exercise_performance.paired_reps_epley_ratio` lê esses mesmos valores (antes usava o RPE médio da exposição); desde o P8 da investigação RPE 10 (2026-10-11) mesma carga de topo + as MESMAS reps cruas emparelhadas com o RPE subindo (RPE das séries emparelhadas quando todas têm, senão o RPE médio) é sem progresso nem regressão (`REASON_SAME_REPS_HIGHER_RPE` = `same_reps_higher_rpe`, decidido em `_same_reps_rpe_change` ANTES do e1RM): 12×35@8 → 12×35@10 é Estável, não `e1rm_down`; reps cruas diferentes seguem e1RM e `reps + RIR`; a recuperação herda via `performance_ratio` (sem `PERFORMANCE_DROP`) | toda tela/IA/alerta (o texto de cada motivo `REASON_*` fica em `STATUS_REASON_TEXTS` do FE, com paridade em `test_dashboard_comparability.test_todo_motivo_do_dono_tem_texto_no_frontend`); tela e IA só exibem `paired_reps`/`paired_top_reps` prontos, com rótulo "reps + RIR" quando `effort_adjusted`; o drill-down recebe `comparable_with_current`/`mismatch_with_current` por exposição (`comparability.mismatch_with_current`, Lote 3c; `fatigue_context_changed` desde o §22) e o `fatigue_context` (rótulo do dono); consumidores do lote leem `_comparable_run`, nunca "as últimas N"; o prompt da Análise por IA (`report_ai.py`) interpola `THRESHOLDS["plateau_min_exposures"]` em vez de escrever "3 ou mais exposições" (IA-06a, Lote 1 LLM) | `_same_load`/`_top_load_reps`/`_paired_top_reps`/`keys_match` copiados | ✅ C12 resolvido no Lote 3a (`comparability.top_load_reps`/`paired_top_reps`/`e1rm_is_decisive` públicas; `exercise_performance` e `exposure_service.paired_top_reps` importam); ✅ C13 resolvido no Lote 3c (FE não refaz `keys_match`) | 3a/3c/P8 | `mesmas_reps_rpe_maior` (allowlist vazia; P8) |
| "Agora", "hoje", semana e fim do dia do usuário | `intelligence/dashboard/time_windows.py` (`user_now`, `user_today`, `user_today_for`, `utc_offset_minutes`, `user_day_start_db`, `user_day_start_db_for` — início do "hoje" do usuário no relógio do banco, consumido pela cota de IA (`nutrition_ai_quota`, §54) e pelo limite do Open Food Facts (`nutrition_external_foods`, §49) —, `user_day_end_db`, `user_day_end_db_sql`, `db_instant_to_user_date`) + `timezones.py` (§4 "Sessões"); nos serviços, `IntelligenceServiceBase._user_today/_days_ago/_trained_at_sql`; vigências por data, `template_assignments._as_moment`. **Início da semana = segunda-feira**: `time_windows` dá o dia do usuário e não tem função de semana; quem calcula a semana usa segunda (`hoje − weekday()` em `training_load_service`, `WEEKDAY()` no SQL semanal de `volume_service`). Para a nutrição o dono da regra é `nutrition/weekly_budget.week_start` (`WEEK_STARTS_ON_WEEKDAY` = 0, §3.7), que deve coincidir com essa convenção e receber o dia já no fuso do usuário | todos os motores | `datetime.now()`, `date.today()`, `CURDATE()` em módulo de motor; "fim do dia" 23:59:59 comparado com instante do banco (UTC) | ✅ B10 resolvido no Lote 2b | 2b | `relogio_sem_fuso` (allowlist vazia) |
| Tonelagem (reps × kg) e sua exibição | `load_conventions.py` (M58): quem entra (`counts_in_tonnage`, `sql_tonnage_sum`, `TONNAGE_EXCLUDED_CONVENTIONS`) e como se exibe (`format_tonnage`: kg inteiros com milhar pt-BR, `TONNAGE_DISPLAY_UNIT`, `TONNAGE_DISPLAY_DECIMALS`) | relatório e dashboard (soma no SQL); resumo de treino por IA (`workout_summary_ai` interpola `format_tonnage`); FE `fmtKgTotal` (mesma forma, só exibe o número da API) | converter a tonelagem para outra unidade (toneladas) em código, prompt ou RAG; prompt com o float cru ("16285.00 kg") | — (dono nasce com a regra; `activities.total_volume_kg` = `SUM(exercises.volume_kg)` ainda não aplica `counts_in_tonnage`, ver §26) | 2026-10-10 | `tonelagem_convertida` (allowlist vazia) |
| Locais de medida por fita (vocabulário, ordem do ENUM, rótulos pt-BR, lado) e origem da kcal do treino | `domain/body_measurement_sites.py` (`MEASUREMENT_SITES`, `SITES_047`/`SITES_ADDED_075`, `SITE_LABELS`, `site_side`, `WAIST_SITE`, `export_for_frontend()` → bloco `body_measurement_sites`); `domain/calories_source.py` (`CALORIES_SOURCE_MANUAL`/`_INTERVALS`, `resolve_calories_source` na leitura — coluna manda, senão igualdade com `activity_intervals_link`, senão `None` —, `source_on_manual_write` na gravação — `None` ao limpar ou quando a kcal é a do link em 0,01, senão `manual` —, `calories_source_label`) | migrações 047/075 (os testes de plano comparam a tupla com o dono), `body_measurements.py` (validação, só reexporta), `body_service` (séries por local), pacote de inteligência e relatório Markdown (rótulo da cintura), FE via `lib/body-measurement-sites.ts` lendo o JSON gerado (`dashboard-body.ts`/`-types.ts` só reexportam); `GET /activities/{id}` devolve `calories_source` e `calories_source_label` prontos; `PATCH /workout-sessions/{id}/meta` lê a kcal do link e grava a origem que `source_on_manual_write` devolve (a tela do registro não envia PATCH quando o valor não mudou) | rótulo de local entre aspas, crase ou como texto JSX fora do dono (py e ts); cópia de `MEASUREMENT_SITES`; rota, tela ou SQL decidindo a origem da kcal (comparar `calories_source` com literal, gravá-la como literal, escolhê-la por condicional, comparar a kcal da atividade com a do link) | — (nasceu sem cópia: `body_measurements.py`, `body_service.py`, `intelligence_package.py`, `markdown_report_service.py`, `dashboard-body.ts`, `DashboardCorpoClient.tsx` (título da cintura) e `DashboardOverviewClient.tsx` passaram a consumir o dono no mesmo PR; a dica do card "Outras medidas" do `DashboardCorpoClient.tsx` e as docstrings de `body_service.measurement_series`/`BodyMeasurementSeriesOut` listavam cinco locais à mão, já sem pescoço e antebraços, e viraram texto genérico na revisão do #556) | Nutrição L0 (2026-10-15; gravação da origem e `origem_kcal` na revisão do #556, 2026-10-16) | `rotulo_site_medida` e `origem_kcal` (allowlists vazias) |
| Natureza e qualidade de um valor exibido (`OBSERVED`/`DERIVED`/`PLANNED`/`PROJECTED`/`ESTIMATED`/`INFERRED`; `HIGH`/`MEDIUM`/`LOW`/`INSUFFICIENT`) | `domain/data_nature.py` (`DataNature`, `DataQuality`), módulo puro. Extraído de `intelligence/dashboard/contracts.py` em 2026-10-16 (L1 de nutrição, §45), que reexporta o mesmo objeto | contratos e serviços do dashboard (17 importadores via `contracts`, sem mudança), motor de nutrição (`TrainingKcal.nature/quality`, `AdaptiveTdee`, `MealNature` reusa DERIVED/ESTIMATED) | enum próprio de natureza/qualidade em motor novo; valores em minúsculas paralelos | **Cópia pré-existente, fora do L1:** os rótulos pt-BR de natureza estão escritos à mão em `frontend/src/lib/dashboard-status.ts` (`DATA_NATURE`, ~l. 459–502), sem export do dono | 2026-10-16 | — |
| Reps e RPE por lado em exercício unilateral (M102, §71): reps oficiais = **menor** lado, RPE oficial = **maior** lado (pior lado), lado vazio ignorado, sem lado vale o enviado; carga única e TOTAL; os motores (contagem, qualificação, queda de reps, e1RM, recorde, carga da próxima série, **tonelagem**) leem só o canônico | `domain/set_sides.py` (`canonical_reps`, `canonical_rpe`, `canonical_from_sides`, `apply_canonical`, `apply_canonical_to_model`, `SIDE_FIELDS`, `format_sides`, `sides_text`, `WORST_SIDE_RULE_TEXT`, `PROMPT_VERSION_SUFFIX`); schema na 100 | gravação (`ExerciseSetIn`/`SessionSetIn`, `exercise_set_update_merge`, `upsert_activity`); leituras devolvem `sides_text` pronto; relatório e prompts interpolam `WORST_SIDE_RULE_TEXT`/`sides_text`; FE só exibe o canônico e os lados da API | `min`/`max`/`LEAST`/`GREATEST` dos lados fora do dono; canônico recalculado na tela | — | M102 | `pior_lado` (allowlist vazia) |

### 3.2 Recuperação / prontidão

Dono: `intelligence/muscle_recovery_service.py` (fórmula central única) + `recovery_projection.py`
para a data futura. §4 "Recovery" já declara o dono do dado.

| Regra | Dono | Proibido fora do dono | Divergências | Lote | Guarda |
|---|---|---|---|---|---|
| Política de dose reduzida (`ceil(S×0,75)`, mín. 1 — decisão 1) | `MuscleRecoveryService.reduced_sets()` / `reduction_policy()` / `reduced_dose_prompt_rule()` / `volume_multiplier_for()` (constantes `REDUCED_DOSE_MULTIPLIER`, `REDUCED_DOSE_MIN_SETS`) | `0.75`, "1–2 séries", 2 séries somadas, orçamento ×0,75 fora do dono; prompt com "1-2 séries / RPE máx. 8-9" à mão | ✅ B2 resolvido no Lote 2a (motor, Modo A/B, templates_engine v1, Full Body, prompts, tela) | 2a | `dose_reduzida`, `dose_reduzida_texto` |
| Teto de RPE por estado (READY/REDUCED_DOSE) | `_RECOMMENDATIONS["…"]["maximum_rpe"]`/`allow_failure_on_last_set` (dono) → `training_safety.readiness_for_status` **lê** dele + `compose` | `READY` fixo na composição; tetos 10/9/8 copiados | ✅ B1 e C10 (tabela) resolvidos no Lote 1a | 1a | — |
| Retrato × projeção (sinais persistem até a próxima sessão — decisão 2) | `MuscleRecoveryService.SIGNALS_POLICY` (`signals_persist_until_superseded`), aplicada por `recovery_projection.py`; janela única `RECOVERY_LOOKBACK_DAYS` + `RECOVERY_GROUP_LIMIT` (defaults de `get_recovery_overview`) | reset próprio "≥72h"; `days`/`limit` diferentes por consumidor | ✅ B3 resolvido no Lote 2a | 2a | `sinais_expiram` |
| Queda de repetições entre séries (sinal `EXCESSIVE_REP_DROP`: 1ª × última série de **trabalho**, mesma carga, `1 − última/primeira > REP_DROP_MIN_FRACTION` = 0,30; **sem** queda quando a última série de trabalho tem RPE ≥ `REP_DROP_IGNORE_FROM_RPE` = 9; só `working` entra; o sinal leva exercício e reps 1ª→última em `blocking_signals[].exercises`) | `VolumeService._rep_drop_from_sets` (`volume_service.py`) → `excessive_rep_drop`/`rep_drop_details` por grupo → `MuscleRecoveryService.build_blocking_signals` | refazer a razão `1 − última/primeira` ou o limiar 0,30 fora do dono; contar ajuste/backoff/técnica na queda; tela/SQL/prompt decidindo "queda" | — (antes: ajuste/backoff entravam e a queda na falha virava fadiga — investigação RPE 10) | RPE 10 Lote 1/P2 (2026-10-11) | `queda_reps_entre_series` (allowlist vazia) |
| Limiares de prontidão (`classify_readiness`/`count_negative_signals`/`build_blocking_signals`/`reconcile_recovery_pct`) | constantes nomeadas em `muscle_recovery_service.py`: `PERFORMANCE_DROP_SIGNAL_RATIO` 0,95, `PERFORMANCE_DROP_BLOCK_RATIO` 0,90, `READY_MIN_ADJUSTED_READINESS` 75, `NO_DIRECT_WORK_BELOW_READINESS` 55, `MUSCLE_PAIN_SIGNAL_MIN` 4, `MUSCLE_PAIN_BLOCK_ABOVE` 6, `NO_DIRECT_WORK_MIN_SIGNALS` 2 (valores inalterados) | cópia do limiar em consumidor | ✅ `dashboard/recovery_signals_service.PERFORMANCE_DROP_RATIO` passou a ler do dono | RPE 10 Lote 1/P2 (2026-10-11) | — |
| Alerta de queda de desempenho (fadiga) | modelo de recuperação (0,95/0,90) | 0,92/0,85 paralelos | ✅ B4, D3: `dashboard_alert_service.py` e `/analytics/dashboard-alerts` apagados no Lote 2c | 2c | `limiar_fadiga` (allowlist vazia) |
| Fator de recuperação de técnica | catálogo de técnicas (`060`) | `_SET_TYPE_FACTORS` com valor próprio (forced 1,05 × 1,20) | ✅ B5 resolvido no Lote 2a (`technique_accounting.recovery_factor` lê só o catálogo; `_SET_TYPE_FACTORS` só cobre série que não é técnica) | 2a | — |
| Horas de recuperação estimadas | `MuscleRecoveryService` | `estimated_recovery_hours` próprio em `/analytics/training-volume` | ✅ B6: campo removido no Lote 2c | 2c | — |
| Dose da sessão vs. dose do dia | `MuscleRecoveryService` (por sessão) | soma por dia em `get_consecutive_day_recovery_checks` | ✅ B7 resolvido no Lote 2a (dose da sessão mais recente de D1) | 2a | — |
| Dor muscular conta só PRIMARY | `VolumeService`/modelo | `PAIN_DAILY_SQL` com todos os papéis | ✅ B8 resolvido no Lote 2a (`_PAIN_COUNTS_AS_PRIMARY`) | 2a | — |
| Recuperação usa `fatigue_weight` de todos os papéis (estabilizador inclusive) e não depende do modo de contagem do volume do Modelo | `VolumeService._resolve_contributions` + `_session_dose_for_item` (dose por papel × `fatigue_weight` cadastrado) | filtrar papéis da dose pelo `VolumeCountingMode` da metodologia; ler o modo do Modelo na recuperação | regra do usuário 2026-10-16 (§38); teste `test_recuperacao_fatigue_weight_todos_os_papeis.py` | §38 | — |
| Fase/prontidão exibida | `MuscleRecoveryService` (`readiness_status`) | segunda classificação de fase/prontidão | ✅ B9: `_recovery_phase`, `home_readiness_pct`/`/analytics/dashboard-home`, `RecoveryPhaseTimeline.tsx` apagados no Lote 2c | 2c | — |
| "Bom para": instante em que o grupo volta a READY (pronto para novo estímulo) | `recovery_projection.ready_forecast` (1º minuto em que `project_recovery_state` classifica READY, busca binária até `READY_SEARCH_HORIZON_HOURS`; READY agora → `READY_NOW_LABEL` "Pronto"; sinal que só nova sessão limpa → sem previsão); fuso: `time_windows.user_wall_to_instant`. Consumidor: `recovery_signals_service._group_out` → `GET /analytics/dashboard/recovery-signals` `groups[].ready_at` (ISO com o fuso do usuário) e `groups[].ready_label`; tela `/dashboard/recovery` (coluna "Bom para") só formata no fuso de `window.timezone` (`fmtWeekdayDateTime`) | `agora + remaining_hours` (100% de tempo, contradiz o "Estado"); somar horas restantes/estimadas a um instante em Python ou TS; tela decidindo "Pronto" | — (nasceu sem cópia, §51; o "Reavaliar em" de `_reassess_after_hours` segue a semântica de "Falta", pré-existente) | 2026-10-17 | `bom_para_paralelo` (allowlist vazia) |
| Fuso nos motores | `time_windows.py` | ver 3.1 | ✅ B10 resolvido no Lote 2b (§12) | 2b | `relogio_sem_fuso`, `trained_at` |
| Prompt de recuperação | interpolado do modelo | TSB "para ajustar"; "72h obrigatórias" (RAG HAB-004) | ✅ B11 resolvido no Lote 2a (TSB só informativo; HAB-004: 72h é teto; `RECOVERY_MAX_HOURS` nomeado no dono) | 2a | — |
| Coeficientes de recuperação | `MuscleRecoveryService._get_personal_coefficient` (neutro, `NEUTRAL_PERSONAL_COEFFICIENT = 1,00`) | ler `muscle_recovery_coefficients` | B12: leitura removida no Lote 2c (0 linhas, sem escritor); **pendente: drop da tabela** (migração destrutiva, fora do 2c) | 2c / migração futura | — |

### 3.3 Progressão / prescrição / segurança

Dono: `domain/progression_engine.py` (v2) via `generation/deterministic_prescription.py`;
segurança em `domain/training_safety.py` + `domain/effective_intensity.py`. §4 "Progressão"
já declara `progression_decisions` como evidência.

| Regra | Dono | Proibido fora do dono | Divergências | Lote | Guarda |
|---|---|---|---|---|---|
| Sugestão de carga/reps (um motor só) | `domain/prescription_service.suggest_for_pair(s)` → `prescribe_pairs` → `progression_engine.prescribe` | `suggest_load_and_reps` (apagado com `progression_service.py`), faixa derivada da sessão, RPE por média; IA por série com carga livre (1c ✅: `engine_decision_context.bound_ai_load`, no passo via `round_to_equipment_step`) | ✅ C1, C2 resolvidos no Lote 1a (IA por série: 1c) | 1a | `motor_legado_carga`, `defaults_prescricao`, `percentual_de_carga` |
| Carga da sessão e adoção pela trilha (carga da sessão = a MAIOR carga L com pelo menos `ceil(n × TOP_LOAD_MIN_SHARE_OF_WORKING_SETS)` (= 0,5) das `n` séries de trabalho em L ou acima E ao menos uma delas cumprindo `reps_min` — a carga de topo precisa se REPETIR, Lote 7/D1; antes, Lote 3/P5, bastava uma série no piso; a mesma carga decide o RESET `load_below_track` (back-off mais leve não zera) e a efetivação do aumento (carga executada ≥ alvo); sem nenhuma no piso, o mínimo, como antes; a mesma regra na calibração; adoção só para cima; com TODAS as `working` abaixo do piso adota se a melhor série ficou a até `BELOW_FLOOR_ADOPTION_MAX_SHORTFALL_REPS` = 3 do piso e fez ≥ `BELOW_FLOOR_ADOPTION_MIN_REPS` = 5 reps; teste de carga é ignorado; redução só pela D10, a partir da carga real) | `progression_engine.session_working_load(working, reps_min)` + `top_load_min_sets` + `adopted_working_load` + `below_floor_load_is_working` + `all_below_reps_min`, aplicada em `progression_evaluator.update_track_execution` (evidência `load_adopted_from_execution`, origem `LOAD_ADOPTION_SOURCE`) | refazer `all(reps < reps_min)` para decidir carga; tela/SQL/lambda decidindo que a carga executada "não vale" | — (M77: antes nunca adotava abaixo do piso e a trilha ficava presa numa carga antiga; Lote 3/P5: antes a carga da sessão era o mínimo das `working` e um back-off leve puxava a carga para baixo; Lote 7/D1: uma única série pesada no piso virava a carga da trilha e as sessões seguintes na carga habitual zeravam por `load_below_track`) | M77 (2026-10-11); RPE10 Lotes 3 e 7 (2026-10-11) | `adocao_carga_abaixo_do_piso`, `carga_topo_repetida` (allowlists vazias) |
| Trilha por contexto de fadiga prévia (identidade `(exercise_id, equipment_id, fatigue_context)`; sem histórico no contexto pedido, parte da trilha do par executada mais recentemente, sem desconto; calibração neutra e fora do histórico de D10/platô) | `progression_engine.track_key`/`resolve_track`/`target_matches` + `TrackState.fatigue_context`/`calibrated_at` (migração 068); contexto da execução: `progression_state.load_fatigue_contexts` (dono `prior_fatigue` sobre a sessão real); contexto planejado: `deterministic_prescription.planned_fatigue_contexts` (`prescribe_pairs(session_order=True)` no Modo B, freeze A/B); `progression_evaluator.counts_for_history` | `tracks.get((ex, eq))`/`tracks[(ex, eq)]` (trilha só pelo par); fator de desconto entre contextos; recalcular fadiga fora do dono | — (nasce com dono) | F1 PR 2 (2026-10-07) | `trilha_sem_contexto` (allowlist vazia) |
| Predicado de qualificação da trilha e dose exigida (nº de `working` ≥ séries da ficha congelada do dia, limitadas a `MAX_SETS_COUNT`; sem ficha congelada, `track.sets_count`; séries extras não mudam `sets_count` — só `ADD_SET`; trilha nova nasce com as séries do Modelo. Lote 3/P4, decisão do usuário: carga de TOPO da sessão ≥ carga da trilha — back-off mais leve não zera; TODAS as `working` ≥ `reps_min` (senão RESET `reps_below_reps_min`); a MELHOR série na carga de topo ≥ `reps_max` (senão RESET `reps_below_reps_max`); esforço na última `working`. A D10 continua exigindo todas abaixo do piso) | `progression_engine.evaluate_track_session` + `required_dose_sets` + `session_working_load`/`top_load_sets`/`best_set`; `progression_evaluator.track_for` (nascimento) | `len(working) < track.sets_count` fora do motor; `sets_count` da trilha subindo por séries executadas (`clamp_sets_count(len(...))`, removido de `update_track_execution`); `any(reps < reps_max)`/`best.reps >= reps_max` fora do motor | — (Lote 2/P3: antes uma sessão com séries extras inflava a trilha e travava em NEUTRAL `sets_below_prescribed_dose`; Lote 3/P4: antes TODAS as séries precisavam de `reps_max` e 12/11/10 ou 17/17/11 zeravam o platô) | RPE10 Lotes 2 e 3 (2026-10-11) | `dose_exigida_series`, `qualificacao_melhor_serie` (allowlists vazias) |
| Regra de excesso de repetições (sessão QUALIFY cuja melhor série na carga de topo fez `reps_max + EXCESS_REPS_FAST_TRACK` = 3 no RPE alvo — RPE dessa série ≥ alvo efetivo da série cobrada — arma o aumento de carga DA TRILHA: um passo de `load_increment` na próxima ficha, sem esperar o platô, e a faixa volta ao piso; ajuste da trilha, NÃO conta para a fase do grupo (só o aumento por platô da trilha conta, Lote 4/P6); dose reduzida difere; sai quando a carga alvo é executada ou expira em `GROUP_EFFECTIVATION_DAYS`; trilha de outro contexto não herda; mesmo mecanismo do aumento por platô da trilha, origem `excess_reps`) | `progression_engine.excess_reps_reached` (sinal `TrackSessionOutcome.excess_reps`) + `arm_excess_reps_fast_track` → `arm_track_load_increase` + `track_pending_load_status` + `track_pending_load` (consumido em `prescribe`, evidência `excess_reps_fast_track`); estado em `TrackState.pending_load_kg`/`pending_load_since` (migração 071) e `pending_load_source` (072); aplicado em `progression_evaluator.evaluate_session`/`update_track_execution` | somar um excesso ao topo da faixa (`reps_max + 3`) para decidir carga em tela/SQL/IA; IA ou tela sugerindo "subir a carga" por repetições sem a decisão do motor | — (nasce com dono) | RPE10 Lotes 3 e 4 (2026-10-11) | `excesso_reps_sobe_carga` (allowlist vazia) |
| Platô de carga POR TRILHA e fase do grupo pela maioria (Lote 4/P6, decisão do usuário, reabre a D2: nas fases de carga `TRACK_LOAD_PHASES` = F2 e F6, com ≥ 1 trilha de carga aplicável, cada trilha com `PLATEAU_MIN_SESSIONS` = 2 sessões QUALIFY consecutivas DELA em ≥ `PLATEAU_MIN_SPAN_DAYS` = 7 dias — NEUTRAL ignorada, RESET zera só a sequência dela; histórico desde a entrada do grupo na fase, sem a sessão de calibração, avaliado com a carga já adotada — arma o PRÓPRIO aumento (um passo, mesmo mecanismo do excesso, origem `track_plateau`; na F6 a cada `F6_LOAD_INTERVAL_DAYS` desde a última mudança de carga da TRILHA), sem esperar os outros exercícios; o aumento só é efetivado com carga executada ≥ alvo e expira em 14 dias (Lote 3/P5); efetivado grava `plateau_load_applied_at`; F2 → F3 quando MAIS DA METADE (`GROUP_PHASE_ADVANCE_TRACK_SHARE` = 0,5, estrito) dos EXERCÍCIOS (`exercise_id`, Lote 7/D2) com trilha ativa de carga do grupo na janela efetivou o aumento por platô desde a entrada na fase — contextos de fadiga/equipamentos do mesmo exercício contam uma vez, e o exercício conta como efetivado se qualquer trilha dele efetivou; o do excesso não conta; o grupo não arma mais `INCREASE_LOAD` (ação legada gravada sai da fila sem promover a fase); a D10 efetivada numa fase de carga não reinicia a fase; as ações de fase sem carga — F1 faixa, F3 técnica, F4/F5 série — seguem pelo platô do GRUPO, `evaluate_group_session`) | `progression_engine.plateau_confirmed` + `load_phase_by_track` + `track_plateau_load_due`/`arm_track_plateau_load` + `arm_track_load_increase` + `pending_load_source` + `track_applied_plateau_load`/`group_load_phase_progress`/`group_load_phase_complete` + `advance_load_phase` + `group_pending_effective` (legado `INCREASE_LOAD` pela maioria); `progression_evaluator.track_plateau_history`/`group_window_tracks`/`evaluate_session` (evidências `track_plateaus`, `track_plateau_load_increase`, `group_phase_advance_by_tracks`, `group_increase_load_moved_to_tracks`); estado em `TrackState.pending_load_source`/`plateau_load_applied_at` (migração 072) | contar sessões qualificantes ou a fração de trilhas fora do motor; platô de grupo decidindo carga; marcar aumento como feito fora do motor; "efetivo por prazo" com a carga parada | — (Lote 3/P5: o `INCREASE_LOAD` do grupo efetivava na primeira execução em qualquer carga; Lote 4/P6: o platô de carga era do GRUPO — o RESET de um exercício zerava todos e nenhum subia; 0 `INCREASE_LOAD` na história do usuário 1) | RPE10 Lotes 3, 4 e 7 (2026-10-11) | `plato_por_trilha` (allowlist vazia) |
| Fechamento avalia contra a ficha congelada (teto, falha, alvo efetivo e séries do `prescription_json`, com o overlay de segurança de hoje — o mesmo de `effective_intensity.intensity_for_row` no Modo Treino; dose reduzida que seguiu a ficha CONTA; item `SKIP`/0 séries treinado → NEUTRAL `prescription_skip`; sem ficha congelada → par composto com READY, como antes) | leitura: `training_history.SESSIONS_SQL` → `FrozenPrescription`; decisão: `progression_evaluator.SessionInputs.evaluation_intensity_for` + `progression_engine.prescription_skipped`/`prescribed_effort_rpe`/`required_dose_sets` | recompor teto com o contrato de recuperação para avaliar sessão que tem ficha; ler `$.effective_rpe_*`/`$.failure_allowed` do snapshot por SQL fora do dono; avaliar pelo `rpe_target`/`sets_count` editáveis de `workout_plan_exercises` quando há snapshot | — (Lote 2/P7: antes a ficha saía em dose reduzida e o fechamento avaliava READY contra as colunas editáveis; tela e avaliação discordavam sobre a mesma série) | RPE10 Lote 2 (2026-10-11) | `ficha_congelada_sql` (allowlist vazia) |
| Incremento de carga e arredondamento ao passo | `progression_engine.load_increment`/`next_load` + `round_to_equipment_step` (público; regra em `session_loads.round_to_step`) + `equipment.load_increment_kg` | +5% sem passo; stepper do FE com passo próprio | ✅ C3 resolvido no Lote 1a; ✅ stepper no 1b (`weight-units.loadStepperStep` usa o `load_increment_kg` da API) | 1a/1b | `carga_percentual` |
| `rpe_target` em texto (parser e corte) | `training_history.parse_rpe_ramp` (alvo = último degrau) → `prescription.parse_rpe` deriva dele; **um** `effective_intensity.clamp_rpe_text` (degrau a degrau) + `clamp_reduced_dose_rpe_targets` (teto do contrato) | `_clamp_rpe_text`, `clamp_rpe_target_text`, `cap_rpe_target_for_reduced_dose` (apagados) | ✅ C4 resolvido no Lote 1a | 1a | `rpe_texto_paralelo` |
| Teto exibido no Modo Treino | `apply_safety_overlay` (prescrição exibida e overlay já compostos, nunca acima do congelado); par trocado na sessão: `suggested-progression.effective_rpe_ceiling` | `overlay ?? prescrição` e `min` entre os dois no FE | ✅ C5 resolvido (backend 1a; FE 1b: `workout-mode-plan-flow.resolveSessionPairLimits` só escolhe a fonte; `intensity-status.ts` só atribui o texto) | 1a/1b | — |
| `safety_event` / `failure_allowed` | `effective_intensity.intensity_for_row` (prescrição congelada composta com recuperação + overlay; sem ficha, o par) — usado por `safety_event_for` e por `failure_allowed_by_logged_exercise` | `analytics._failure_allowed_for_rows` só por equipamento | ✅ C6 resolvido no Lote 1a | 1a | — |
| Origem da negação da falha e semântica do RPE 10 (`failure_denied_by` ∈ None/`PAIR`/`RECOVERY`; empate = `PAIR`): par que proíbe a falha → `RPE_ABOVE_SAFE_CEILING` (laranja, vence os outros status, log de observabilidade); falha negada **só** pela recuperação → `RPE_ABOVE_RECOVERY_TARGET` (informativo, status normal `FAILURE_NOT_ALLOWED_REACHED_CEILING`/`BELOW_TARGET`, sem log) | `training_safety.failure_denial_origin` (usada por `compose`), congelada em `prescription.build_prescription` (`failure_denied_by`; snapshot antigo: `prescription.failure_denied_by_of` deriva de `safety_constraints.max_safe_rpe`), `apply_safety_overlay` (par de hoje vence); `progression_engine.safety_event_type_for`/`intensity_status_for`/`evaluate_track_session`; `effective_intensity.safety_event_for`/`session_intensity_status`; rótulos em `domain/intensity_status_labels.py` → `motor-constants.generated.json` → `intensity-status.ts` (só cor e banner) | decidir no FE/analytics se o evento é laranja; rótulo de intensidade/evento escrito à mão no FE | — (antes: RPE 10 em dose reduzida gravava o evento de segurança laranja) | RPE 10 Lote 1/P1 (2026-10-11) | — (códigos/rótulos com paridade em `test_m76_lote1b_espelhos_fe.py`) |
| Status por SÉRIE no Modo Treino (status de **esforço**: esforço atingido / abaixo do alvo / informativo / laranja só do par), mesma regra do status da sessão | `progression_engine.set_intensity_status` (núcleo único `_intensity_status_core`, compartilhado com `intensity_status_for`) → `effective_intensity.set_intensity_reading` (série `working` contra a intensidade efetiva resolvida) → devolvido por `POST /workout-sessions/{id}/exercises/{eid}/sets` (`intensity_status` + `intensity_status_label`, sem coluna nova) e entregue pronto ao prompt da IA por série; rótulos em `intensity_status_labels` | FE/IA decidindo se a série atingiu o alvo; status por série reimplementado fora do motor | — (antes: o card não mostrava status por série nem a decisão vigente) | RPE 10 Lote 6/P9 (2026-10-11) | — (mesmo núcleo testado contra `intensity_status_for` em `test_rpe10_lote6_uma_voz.py`) |
| Reps de UMA série contra a faixa (abaixo da faixa / dentro / no teto / acima), ao lado do status de esforço | `progression_engine.set_reps_position` (`REPS_BELOW_FLOOR`/`REPS_IN_RANGE`/`REPS_AT_TOP`/`REPS_ABOVE_TOP`) → `effective_intensity.set_intensity_reading` (`reps`, `reps_range`; série `working`) com a faixa da ficha (`effective_intensity.plan_reps_range`: prescrição congelada, senão `workout_plan_exercises.reps_target`) no salvamento da série (`reps_position` + `reps_position_label`) e a da avaliação (`engine_decision_context.evaluation_reps_range`: decisão do motor, senão `reps_target` do payload) no prompt `set-eval-v5`; rótulos em `intensity_status_labels.REPS_POSITION_LABELS` → `motor-constants.generated.json` (`reps_position_labels`) → `set-engine-status.ts` (selo neutro) | comparar reps com a faixa no FE ou no prompt; ler o status de esforço como alvo geral da série; rótulo escrito à mão | ✅ rótulos "Alvo atingido"/"Abaixo do alvo seguro" trocados por "Esforço atingido"/"Esforço abaixo do alvo" no dono (o mesmo rótulo na série e na sessão/dashboard) | set-eval-v5 (2026-10-12) | `rotulo_status_serie` (allowlist vazia) |
| Limiar de dor muscular ALTA (acima de `MUSCLE_PAIN_BLOCK_ABOVE` = 6 → NO_DIRECT_WORK) e a regra da IA por série (dor alta → `DECREASE_LOAD` com carga abaixo da executada no passo do equipamento, ou encerrar o exercício; o status de esforço não justifica manter) | `muscle_recovery_service.muscle_pain_blocks_direct_work` (predicado único, usado por `classify_readiness`, `reconcile_recovery_pct` e pelo prompt `set-eval-v5`, que interpola `MUSCLE_PAIN_BLOCK_ABOVE`) | comparar `muscle_pain` com número escrito à mão; limiar de dor escrito no prompt | — (antes: o prompt v4 só dizia "dor muscular alta", sem limiar, e respondia HOLD com dor 9/10) | set-eval-v5 (2026-10-12) | `limiar_dor_muscular` (allowlist vazia) |
| Categorias da IA por série (vocabulário do motor: `HOLD`/`INCREASE_LOAD`/`DECREASE_LOAD`, rótulos de `DECISION_LABELS`) e coerência com a carga final (categoria = direção da carga sugerida, já no passo/direção do motor, × executada; sem carga sugerida vale a da IA; desde a set-eval-v6, quando a regra da próxima série se aplica, a categoria é a direção dela) | `domain/set_evaluation_categories.py` (`SET_EVALUATION_CATEGORIES`, `category_for_load`; texto da regra para o prompt em `category_rule_text`) → enum do schema strict e prompt `set-eval-v6` (`exercise_set_evaluation_ai`) → `motor-constants.generated.json` (bloco `set_evaluation`) → `set-engine-status.ts` (só cor por direção) | vocabulário próprio da IA (PROGREDIR/APRIMORAR TECNICA/REDUZIR/REGREDIR); rótulo de ação escrito à mão no FE (`PROGRESSION_DECISION_LABELS` agora lê `progression_decision_labels` do JSON) | ✅ cópia manual de `DECISION_LABELS` em `exercise-progress.ts` removida | RPE 10 Lote 6/P9 (2026-10-11) | `vocabulario_ia_serie` (allowlist vazia) |
| Carga da PRÓXIMA série dentro da sessão (regra do usuário de 2026-10-12): dor articular ou dor muscular acima de `MUSCLE_PAIN_BLOCK_ABOVE` → reduzir (acima de tudo); mais de `NEXT_SET_INCREASE_ABOVE_REPS` = 15 reps (qualquer RPE, inclusive sem RPE) → aumentar; `NEXT_SET_INCREASE_MIN_REPS_LOW_RPE` = 12 reps ou mais com RPE abaixo de `NEXT_SET_LOW_RPE_BELOW` = 9 → aumentar; RPE `FAILURE_RPE` = 10 com menos de `NEXT_SET_DECREASE_BELOW_REPS` = 6 reps → reduzir; senão manter. Rampa PLANEJADA de RPE na ficha (`"8-9-10"`): o limiar da condição de 12+ reps vira `min(NEXT_SET_LOW_RPE_BELOW, RPE planejado da série)` (`planned_ramp_rpe`, parser único `training_history.parse_rpe_ramp` + `PrescribedSpec.rpe_target_for_set`; motivo `NEXT_SET_WITHIN_PLANNED_RAMP`). Um passo do equipamento (executada ± passo, arredondada ao passo); só série `working` com carga; fora `assisted`/`bodyweight` | `domain/next_set_load.py` (`suggest_next_set_load`, `NextSetLoad`, motivos `NEXT_SET_*` e rótulos em `reason_labels`) → `engine_decision_context.next_set_load_for` (passo e convenção do par: `load_pair_step_context`, `load_conventions.pair_load_convention`) → `bound_ai_load` (a carga final da IA por série É a da regra; ajuste `NEXT_SET_LOAD_RULE` em `load_adjustments[]`) e `apply_engine_load_bound` (categoria = direção da regra) → prompt `set-eval-v8` (sugestão pronta + "Regra que casou" por `describe_matched_rule` + dor por `describe_pain_rule` + conjunto vigente por `describe_ruleset` com a versão + linha da rampa planejada; §59.3) → salvamento da série (`workout_sessions.add_session_set`, campo `next_set_load`) → `motor-constants.generated.json` (bloco `next_set_load`) → `set-engine-status.nextSetLoadView` ("Próxima série: Aumentar carga → 22,5 kg" + motivo) e pré-preenchimento da carga em `PlanExecutionScreen.tsx` | limiar da regra escrito à mão (código, SQL, FE, prompt, RAG); IA subindo até o alvo do motor ou contra a regra; FE decidindo a carga da próxima série | — (antes: a IA podia subir até o alvo da SESSÃO dentro da sessão, e sem IA a tela repetia a carga anterior) | set-eval-v6 (2026-10-12) | `carga_proxima_serie` (allowlist vazia) |
| Regras de carga da PRÓXIMA série (R1, 2026-10-20, §59): conjunto de regras ORDENADO (`RuleSet` = piso/teto de reps + `Rule`s), a PRIMEIRA que casa vence; cada regra = posição da série entre as `working` do exercício (`SetPosition`: qualquer/1ª/2ª/3ª em diante, 1-based) × contexto de fadiga prévia do exercício (`FatigueCondition` = faixas de `domain/prior_fatigue.py`, lida ANTES da 1ª série, a mesma chave da trilha; desconhecida só casa "qualquer") × condição de reps (número ou tokens `floor`/`ceiling` = piso/teto do conjunto) × condição de RPE (número ou token `max` = teto efetivo da série; sem RPE não casa) × `respect_planned_ramp` (limiar superior vira `min(limiar, RPE planejado)`) × ação (`HOLD`/`INCREASE_LOAD`/`DECREASE_LOAD`) × magnitude (`steps` n = n passos; `percent` p = carga × (1 ± p/100) arredondada ao passo, ≥ 1 passo na direção — aplicado SÓ no dono). Dor articular/muscular acima de `MUSCLE_PAIN_BLOCK_ABOVE` reduz 1 passo ANTES das regras (não configurável); nenhuma casando → manter (`NEXT_SET_WITHIN_PLANNED_RAMP` se a rampa barrou alguma, senão `NEXT_SET_WITHIN_RULE`); regra sem `reason_code` → `NEXT_SET_RULE_MATCHED` com rótulo = `describe_rule`. `DEFAULT_RULESET` (versão 0) = a regra de 2026-10-12 (3 regras, piso 6, teto 12); limites do cadastro em `RULESET_MAX_RULES`, `MAGNITUDE_*`, `REPS_FLOOR_MIN`, `REPS_CEILING_MAX`, `RULE_RPE_MIN/MAX` | `domain/next_set_load.py` (`evaluate_ruleset`, `SetFacts`, `DEFAULT_RULESET`, `describe_rule`/`describe_ruleset`, `validate_ruleset`/`ruleset_errors`, `ruleset_from_dict`/`ruleset_to_dict`, `export_for_frontend` com `rule_vocabulary`/`rule_limits`/`default_ruleset`) → `engine_decision_context.next_set_load_for` (só monta `SetFacts`: índice, faixa, teto, rampa, passo/convenção) → salvamento da série (`workout_sessions._evaluate_set`: índice = `COUNT` das `working` anteriores + 1, faixa por `session_fatigue_reader.load_session_prior_fatigue`, teto `intensity.ceiling`) e IA por série (`workout_ai_generation_core.dispatch_evaluate_set`: índice = `payload_working_set_index` + 1, faixa por `session_id`/`exercise_id` do payload com ownership pelo mesmo leitor, teto `(logged or intensity).ceiling`) → `NextSetLoad` aditivo (`rule_position`, `rule_text`, `ruleset_version`, `magnitude`) → `motor-constants.generated.json` (bloco `next_set_load`) → tela de cadastro (R4) só monta Selects com `rule_vocabulary` | condição de regra avaliada fora do dono (`rule.reps.op ==`, `reps_condition … > 12`, `magnitude.value / 100`); percentual de carga aplicado fora do dono (`carga * (1 ± p/100)`, py e ts); conjunto padrão redefinido fora do dono (`DEFAULT_RULESET =`, `RuleSet(reps_floor=<n>`); regra numérica na tela; prompt com número à mão | — (R2, §59.1: conjunto gravado e versionado na migração 088, loader `next_set_load_rules.py` — sem linha/falha = padrão —, rotas `/next-set-load-rules` com validação só pelo dono, faixa de fadiga lida só quando `ruleset_uses_fatigue`; a tela é o R4; R3, §59.3: o prompt `set-eval-v8` cita `rule_text` e `describe_ruleset` do conjunto avaliado, repassado por `dispatch_evaluate_set`) | R1, R2, R3 (2026-10-20) | `regra_carga_cadastrada` (nasce vazia), `carga_percentual` (+ dono `next_set_load.py`, + ts), `carga_proxima_serie` (+ `DEFAULT_RULESET`/`RuleSet(reps_floor=`) |
| Sinal de recuperação que cortou o teto (banner do Modo Treino e briefing: "Teto 9 hoje pela recuperação: queda de repetições no exercício X (12 → 8 reps) em 03/10") | `domain/recovery_signal_summary.py` (`RECOVERY_SIGNAL_LABELS`, `summarize_signals` sobre `ReadinessContract.blocking_signals` do dono da prontidão) → `progression_engine.prescribe` (evidência `recovery_signals`) / chamador sem decisão → `prescription.build_prescription` congela `recovery_signals` (aditivo) → `describe_adjustment` → FE `intensity-status.ts` (`formatRecoverySignals`/`recoveryCeilingText`: só DD/MM) | texto do sinal escrito no FE; FE decidindo qual sinal cortou o teto | — (antes: o banner não dizia o sinal) | RPE 10 Lote 6/P9 (2026-10-11) | — |
| Cargas de aquecimento / ajuste / drop | `domain/session_loads.py` (`session_load_plan`, passo do equipamento); exposto em `prescription.{load_anchor_kg, load_increment_kg, warmup_loads_kg, feeler_load_kg, feeler_loads_kg, application_loads_kg}` e no briefing | fatores escritos à mão no FE ou fora do dono | ✅ C7 resolvido (backend 1a; FE 1b: lê as listas da API quando a âncora é a prescrita; âncora dinâmica da sessão usa o espelho `workout-mode-load-suggestions.ts` com fatores de `motor-constants.generated.json` e paridade em `session-loads.parity.json`, ambos exportados pelo backend; desde a §57 o espelho e o resto do núcleo do Modo Treino saem também num bundle (`workout-mode-core/`) consumido pelo celular e pelo relógio, com a mesma paridade + golden; reps do aquecimento genérico em `session_loads.GENERIC_WARMUP_REPS`) | 1a/1b, §57 | `fatores_carga_sessao` |
| Convenção de carga (`load_convention`) | `load_conventions.py` + motor v2 | ignorar `assisted`/`per_side` | C8 **pendente** (não coube no 1a: exige ler a convenção por par em `prescribe_pairs` com guarda de coluna; 3/542 exercícios) | 2+ | — |
| "Ajuste de hoje" | decisão v2 congelada (`exercise_briefing.describe_adjustment`) | indicador legado (`progression`) virando badge | ✅ C9 resolvido no Lote 1a | 1a | — |
| Constantes de segurança/janela (`DEFAULT_MAX_SAFE_RPE`, `REDUCED_DOSE_MULTIPLIER`, janela 21) | `training_safety.DEFAULT_MAX_SAFE_RPE`; `MuscleRecoveryService._RECOMMENDATIONS`; `training_history.DEFAULT_SESSION_WINDOW` | cópias (`progression_evaluator.py` ✅, `progression_state.SESSION_WINDOW` ✅, `exercise_briefing.HISTORY_WINDOW` ✅, `ACTIVE_TRACK_WINDOW_SESSIONS` apagado ✅; `training-safety.ts` ✅ no 1b (lê `motor-constants.generated.json`, exportado de `training_safety.py`); **pendente**: `REDUCED_DOSE_MULTIPLIER` (Lote 2)) | C10 | 1a/1b/2 | `dose_reduzida` |
| Prompts/RAG de progressão | decisão do motor pronta via `engine_decision_context.py` (ação, carga/reps alvo, passo do equipamento) | "+5%", "2,5%-5%" e qualquer incremento percentual em prompt/RAG; IA inferindo progressão | C11 | 1 (1c ✅: avaliação de série, resumos de exercício e de treino, `PROGRESSAO.md`, `workout_plan_templates_engine.py`, `MUSCLE_FIT.md`; 1a ✅ `workout_plan_ai.py` (incrementos do motor interpolados); 5a ✅ `abc_workout_ai.py` apagado) | `percentual_de_carga` |
| Desempenho/volume por grupo | `muscle_group_performance.py`, `muscle_group_progression.py` (métricas distintas, aceitas) consumindo `e1rm` do dono | cópias de Epley/RIR e de `comparability` | ✅ C12 resolvido no Lote 3a | 3a | `epley`, `rir` |
| Rótulos de status de progressão por exercício | `domain/progression_status_labels.EXERCISE_PROGRESSION_LABELS` (com acento); `metrics_registry.EXERCISE_PROGRESSION_STATUS_LABELS` e `exercise_briefing.PERFORMANCE_LABELS` derivam dele; FE via `motor-constants.generated.json` (`dashboard-status.ts`, `exercise-progress.ts`) | rótulo literal ("Estável", "Suspeita de platô", "Não comparável"...) fora do dono | ✅ C13 resolvido no Lote 3c | 3c | `rotulo_progressao` |
| Janela do briefing | 90 dias (`time_windows`) | 91 dias | C14 | 1 | — |

### 3.4 Recomendação / insights

| Regra | Dono | Proibido fora do dono | Divergências | Lote | Guarda |
|---|---|---|---|---|---|
| Ranking de equipamento | `domain/equipment_ranking.py` (`DEFAULT_STARS`/`MIN_STARS`/`MAX_STARS`; ausente = 3). Preferência do usuário por par exercício+equipamento (entrada do ranking) gravada em `/me/equipment-exercise-preferences`; consumidores só projetam/gravam a nota: tela "Preferências de Treino" (`/preferencias-treino`, 2026-10-13) via `GET /me/training-preferences` (`equipment_exercise_preferences.py`, só leitura, `stars` cru e `default_stars` do dono, ordem alfabética de exibição) e Domínio de Exercícios (só admin) | chave própria no FE; prompt "ordenados do menos usado"; FE/endpoint ordenando por nota ou aplicando o neutro de 3 | D1 | 4 | — |
| Limiares e horizontes de insight | `metrics_registry.THRESHOLDS` | `+7/+14/+28`, `horizon=3`, `72.0` soltos em `insight_rules.py` | D2 | 4 | — |
| Alertas do dashboard antigo | `insight_rules` (`/analytics/dashboard/insights`) | `dashboard_alert_service.py` | ✅ D3: apagado no Lote 2c | 2c | `limiar_fadiga`, `normalizacao_semanal` |
| Similaridade / alternativas | — | `ExerciseSimilarityService`, `recommend_alternatives`, `recommend_focus_groups` | ✅ D4: apagados no Lote 2c | 2c | — |
| Recomendação de cardio: PAI mínimo, escada de Z2 (min), faixa de Z2 em % do VO2max, protocolo de HIIT, Z2 de correção de tendência, mínimo de ocorrências da memória adaptativa, exemplo de evidência | `domain/cardio_policy.py` (`PAI_TARGET_MIN`, `Z2_DURATION_LADDER_MINUTES`, `Z2_TREND_CORRECTION_MINUTES`, `Z2_VO2MAX_PERCENT_RANGE`, `HIIT_*`, `ADAPTIVE_MIN_OCCURRENCES`, `EXAMPLE_EVIDENCE_*`, `CONTINUOUS_PROGRESSION_STEP_MINUTES` (M98 C3a, só RAG); helpers de texto `preference_ladder_text`, `hiit_protocol_text`, `active_recovery_minutes`) — decisão do usuário 2026-10-08: cardio é motor | `cardio_agent_ai.SYSTEM_PROMPT` só interpola | número de cardio escrito à mão em prompt/código | ✅ IA-06 (c): `SYSTEM_PROMPT` interpolado, texto renderizado idêntico | IA-06 | `numeros_prompt_cardio` (allowlist vazia) |
| Zonas de FC do atleta: vocabulário Z1–Z5 e rótulo; precedência Intervals → FCmáx manual → sem zonas; % da FCmáx de cada zona no fallback; FCmáx manual aceita (validação da escrita no perfil, C1); FC → zona; forma das zonas resolvidas na API (C1) | `domain/hr_zones.py` (`HR_ZONE_NUMBERS`, `MAX_HR_ZONE_BOUNDS_PERCENT`, `ZONE_SOURCE_PRECEDENCE`, `resolve_athlete_zones`, `zone_of_hr`, `validate_manual_max_hr`, `athlete_zones_payload`); bloco `hr_zones` do `motor-constants.generated.json` (rótulos e a faixa da FCmáx manual). Consumidores da C1: `PUT /users/me` e `PUT /users/{id}` (`users._max_hr_set`, coluna `users.max_hr_bpm_manual` da 096) e `GET /users/me/hr-zones`; o editor do Modelo e o perfil só exibem (`lib/hr-zones.ts`). C2: zonas do Intervals (`zones_from_intervals`, `INTERVALS_ZONE_MAP` 7 → 5, `INTERVALS_Z1_LOWER_PERCENT`, `pick_reference_sport`/`INTERVALS_REFERENCE_SPORT_TYPES`) e tempo em zona (`parse_intervals_zone_seconds`, `app_zone_seconds`); leitura em `athlete_hr_zones.load_athlete_zones` | % de FCmáx ou limite de zona escrito fora do dono (py, ts, prompt, RAG) | ✅ C3a (§69.4): os 15 trechos "N% da FCmáx" de `rag/markdown/CARDIO.md` viraram a zona-alvo do sistema (`{{cardio.zona_alvo_continuo}}`, em bpm pelas zonas do atleta) | M98 C0/C1/C2/C3a (§69) | `zonas_fc_paralelas` |
| Aderência do cardio, planejado × realizado: tempo cumprido, % na zona-alvo (Normal), zona alta e baixa (HIIT), limiares do status, resumo semanal | `domain/cardio_adherence.py` (`evaluate`, `summarize_weeks`, `ADHERENT_TIME_MIN_PCT`, `ADHERENT_ZONE_MIN_PCT`, `PARTIAL_TIME_MIN_PCT`); o casamento é o de `intervals_hr_match` (âncora: fim da sessão de força) e o tempo por fase do HIIT é de `cardio_prescription.hiit_phase_seconds`. Consumidores: `cardio_adherence_service` → `GET /integrations/intervals/cardio-adherence` e (C4) `cardio_service.adherence_view` → `GET /analytics/dashboard/cardio` (rótulos `STATUS_LABELS`, minutos por `cardio_prescription.minutes_from_seconds`). Âncoras do casamento: `ANCHOR_STRENGTH_END` (em uso) e `ANCHOR_LOCAL_DAY` (C4, proposta pendente: `intervals_hr_match.match_local_day`, dia de descanso; §69.6) | limiar de aderência ou % de zona escrito fora do dono; terceiro critério de casamento fora de `intervals_hr_match` | — | M98 C2 (§69.3), C4 (§69.6) | `aderencia_cardio_paralela` |
| Envelope do agente de cardio (M98 C3a): horizonte da recomendação, projeção do plano nos próximos dias (ficha da data > dia da semana do slot > sequência do Modelo), dia de perna, sinais de recuperação ruim (TSB, queda de HRV, FC de repouso alta, perna sem liberação) e faixa de carga, sessão intensa já realizada, modos e zona máxima por dia, tetos da semana (sessões, HIIT, minutos) e a reaplicação DEPOIS do LLM | `domain/cardio_envelope.py` (`build_envelope`, `apply_envelope`, `assess_recovery`, `project_plan_slots`, `is_leg_day`, `is_high_intensity`, `RECOMMENDATION_HORIZON_DAYS`, `WEEKLY_HIIT_MAX`, `WEEKLY_CARDIO_SESSIONS_*`, `HIIT_MIN_TSB`, `TSB_HIGH_RISK_BELOW`, `BAND_*`, `HRV_DROP_BAD_PCT`, `RESTING_HR_RISE_BAD_BPM`, `LEG_MUSCLE_GROUPS`, `HIGH_INTENSITY_*`, `CONTINUOUS_SESSION_MAX_MINUTES`). Consumidores: `cardio_agent_ai.build_envelope` (antes do LLM, vai no contexto), `cardio_recommendation_v3.process_llm_output` (depois do LLM), `cardio_context_v3` (projeção do plano e marca `intense`), `SYSTEM_PROMPT` e `CARDIO.md` só interpolam. **C5 (Modelo):** `template_signals` (no Modelo só vale a faixa do TSB), `project_template_slots` (dia da semana sem repetir ou sequência do ciclo; âncora `TEMPLATE_ANCHOR_MONDAY`), `build_template_envelope`/`apply_template_envelope` (mesmas regras sobre o ciclo; o último slot é a véspera do 1º); consumidor `periodization/cardio_plan` (prompt `periodization-v13` interpola, validação reaplica) | veto de HIIT, teto semanal, limiar de TSB/HRV/FC de repouso, horizonte ou lista de grupos de perna fora do dono (py, ts, prompt, RAG) | — | M98 C3a (§69.4), C5 (§69.7) | `envelope_cardio_paralelo`, `numeros_rag_cardio` |
| EPOC e PAI acumulados de 7 dias ESTIMADOS do tempo em zona de FC do Intervals (M99): pesos por minuto em cada uma das 7 zonas do Intervals, janela móvel de 7 dias (o dia e os 6 anteriores), janela de leitura, arredondamento, "sem valor" (janela sem tempo em zona = None) e versão do modelo | `domain/cardio_load.py` (`EPOC_ZONE_WEIGHTS`, `PAI_ZONE_WEIGHTS`, `ROLLING_WINDOW_DAYS`, `activity_read_start`, `estimate_days`, `CARDIO_LOAD_MODEL_VERSION`). Leitura: `intervals_store.ZONE_SECONDS_SQL`/`zone_seconds_rows` + `effort_estimates` (sem regra). Consumidores: `GET /effort-metrics` (Indicadores físicos), contexto do agente de cardio v1 (`cardio_context_service`, também o relatório) e v3 (`cardio_context_v3`, `effort_metrics_estimated`), card de PAI do `/dashboard/cardio` (`cardio_service.load_pai`) | pesos de EPOC/PAI, janela de 7 dias ou fator `pai * 0.x` em rota, SQL, prompt ou TS; tela recalculando o estimado | — (nasce com dono; o CRUD manual `/manual-effort-metrics` saiu, a tabela ficou como histórico) | M99 (§70) | `pesos_esforco_paralelos` (allowlist vazia) |
| O que é cardio (atividade do Intervals e sessão do app) | `intervals_activity_kind.py` (`classify`, `is_cardio`). Consumidores: `cardio_service` (hub), `cardio_adherence_service`, `cardio_context_v3` e (C4) `analytics.py` (`count_cardio_activities`, `summarize_cardio_periods` sobre `activities.activity_type` + `name`) | `_is_cardio_activity`/`_CARDIO_PATTERNS` próprios, `LIKE '%cardio%'` | ✅ zeradas na C4: o dashboard ao vivo de `intervals_integration.py` saiu e `analytics.py` classifica pelo dono | M98 C2/C4 (§69.3, §69.6) | `cardio_paralelo` |

### 3.5 Criação de fichas / Modelo / volume

Dono do volume: `intelligence/methodology_volume.py` (+ `muscle_volume_class_ranges`, o cadastro de `primary_only`; Fase 6. Modos ponderados nas filhas da 073, §39).
§4 "Template/ciclo" e "Metas" já declaram as tabelas.

| Regra | Dono | Proibido fora do dono | Divergências | Lote | Guarda |
|---|---|---|---|---|---|
| Modo de contagem do volume da metodologia (`primary_only` × `primary_secondary`): papéis contados por modo, peso de cada contribuição, sessão que conta na frequência por secundário (`SECONDARY_SESSION_MIN_SETS` = 1 série), exercício só PRIMARY | `methodology_volume`: `VolumeCountingMode`, `COUNTED_ROLES_BY_MODE` (estabilizador nunca), `counted_contributions` (1 em `primary_only`; `stimulus_weight` cadastrado em `primary_secondary`, primário inclusive), `SECONDARY_SESSION_MIN_SETS`, `PRIMARY_ONLY_WEIGHT`; leitura em `methodology_volume_service.load_volume_contributions` (papel + `stimulus_weight`, `is_active=1`) | filtro `role='PRIMARY'` reescrito fora do loader para o volume da metodologia; peso de secundário escrito à mão (0,5); fração arredondada para série inteira; secundário contando exercício | ✅ Lote 1 do modo de contagem (2026-10-16, §38). **EXG-014 vale para `primary_only`**; `primary_secondary` é opção do usuário, com faixas próprias. ✅ Lote 2 (§39): o modo é gravado na versão do Modelo (073) e editor/diagnóstico/avisos avaliam nele; validador da IA, complemento e progressão seguem em `primary_only` até os Lotes 4/5 | §38, §39 | `modo_contagem_volume`, `peso_secundario`, `faixas_sem_modo` (allowlists vazias) |
| Faixas por frequência e meta semanal POR MODO DE CONTAGEM (qual linha do cadastro vale para cada modo; marca "cópia não calibrada") | `methodology_volume_service.load_volume_classes(cur, modo)`. Em `primary_only`, lê as tabelas da 057 como antes: `muscle_volume_class_ranges` e `muscle_volume_classes.weekly_*`, que são o cadastro de `primary_only` por definição, intocadas. Nos outros modos, lê as filhas `muscle_volume_class_mode_ranges WHERE counting_mode = modo` e `muscle_volume_class_weekly_targets` por LEFT JOIN; sem linha = sem faixa ou meta, nunca a de outro modo. Escrita: CRUD admin `muscle_volume_classes.py` (`?counting_mode=`, `copied_from_counting_mode`/`uncalibrated`) | ler as filhas sem `counting_mode = …`; usar a faixa de `primary_only` como fallback do modo ponderado; gravar modo ponderado nas tabelas da 057; frontend decidir "não calibrada" por conta própria | ✅ Lote 2 do modo de contagem (2026-10-16, §39) | §39 | `faixas_sem_modo` (allowlist vazia) |
| Rótulo da intenção da metodologia de volume (`standard`/`specialization`/`maintenance`/`cutting`: "Padrão", "Especialização", "Manutenção", "Cutting") | **dono futuro**: `intelligence/methodology_volume.py` (um `INTENT_LABELS` ainda a criar, exportado no bloco `methodology_volume` que já existe em `motor-constants.generated.json`; o código já é `MethodologyIntent`) | rótulo escrito à mão no FE ou no relatório | **Cópias atuais, divergentes entre si**: `report/markdown_renderer.py` (`INTENT_LABELS`, "Cutting (definição)") × `frontend/src/lib/methodology-volume.ts` (`METHODOLOGY_INTENT_OPTIONS`, "Cutting"); são as 2 entradas da allowlist de `rotulo_estrategia` (a grafia coincide com a estratégia de nutrição) | backlog **M84** (não feito no L1 de nutrição) | `rotulo_estrategia` (inventário inicial, 2 arquivos) |
| Normalização semanal (ciclo ≠ 7) | `methodology_volume`: `evaluation_window_days` + `weekly_normalization()` (planejado da semana) + `calendar_weekly_rate()` (realizado em N dias) + `weekly_window_days()` | `* 7 / ciclo`, `7.0 / dias`, `max(7, ciclo)` fora do dono | ✅ E1, E2 (Lote 4a) | 4a | `normalizacao_semanal` (sobra 1 permanente) |
| Payload de ativação (com técnica) | `technique_volume.activation_payload` (`{mapping_id, sets_count, advanced_technique, advanced_technique_applications}`, o shape do item do Modelo; FE: `lib/activation-payload.ts`); séries com técnica em `planned_item_sets` dentro do VolumeService | `{mapping_id, sets_count}` sem técnica | ✅ E3 resolvido no Lote 4b | 4b | `payload_ativacao_sem_tecnica` |
| Alvo por slot em séries equivalentes e status da ativação da sessão | `mode_b.planned_slot_targets` (alvo = `total_equivalent` planejado); `volume_service.assess_activation_against_target` (`classify_planned_volume_status` / `classify_weekly_volume_status`) | séries brutas comparadas com ativação ponderada; limiares 0,9/0,5/6/9 no FE | ✅ E4 resolvido no Lote 4b | 4b | `limiar_ativacao_fe` |
| Séries do slot recolhido do Modelo | `direct_sets` da sessão do bloco no preview da metodologia (`methodology_volume`) | soma de `sets_count` por grupo do item no FE | ✅ E5 resolvido no Lote 4b | 4b | — |
| Metodologia do realizado (7 dias) | `intelligence/methodology_realized.py` (`GET /analytics/methodology-realized`), com `block_of_group`/`_compare`/`frequency_bucket_for` do motor e `calendar_weekly_rate` (régua do realizado, Lote 4a) | faixa por frequência, comparação e fatia → bloco no FE | ✅ E6 resolvido no Lote 4b | 4b | `metodologia_no_fe` |
| Tempo de treino do Modelo | `periodization/time_estimate.py` (`estimate_slot_time`; preview devolve `slot_times[]`); espelho `template-time-estimate.ts` com paridade completa | premissas 3 s/rep, 10 reps, 0 s sem descanso escritas fora do dono | ✅ E7 (Lote 4c) | 4c | `premissas_tempo` |
| Prescrição de cardio do item do Modelo: modos Normal (`continuous`) e HIIT, campos e unidades (aquecimento/tempo/recuperação em min, zona baixa/alta em s), obrigatórios por modo, zona alta > baixa, mínimos e tetos por campo e da sessão, padrões do editor, tempo total (`total_seconds`), texto canônico, resumo do item recolhido (C1), normalização para a coluna e forma canônica da versão | `domain/cardio_prescription.py` (`validate`, `validation_error`, `clamp_fields` (C3a: correção da saída do agente ao limite do campo), `total_seconds`, `total_minutes`, `minutes_label` e `minutes_from_seconds` (C3b: o "N min no total" das telas), `describe`, `describe_short`, `describe_steps` e `same_prescription` (C3b: etapas do Modo Treino e idempotência do "aplicar no Modelo"), `normalize_fields`, `canonical_extras`, `draft_cardio`, `FIELD_LIMITS`, `DEFAULTS`, `limits_text` e `default_prescription` (C5: texto dos limites e padrão com o modo, para os prompts do agente e da periodização)); bloco `cardio_prescription` do `motor-constants.generated.json`; espelho `lib/cardio-prescription.ts` (tempo, `describe` e `describe_short`; paridade em `template-time-estimate.parity.json` e `cardio-prescription.parity.json`); validação no editor por `POST /workout-plan-templates/cardio-prescription-check` (C1) | `cardio_duration_minutes` comparado a número ou × 60, constantes `CARDIO_*`/`HIIT_*` de prescrição com número, padrão/teto à mão no editor ou prompt | ✅ resolvidas na C0: `cardio_duration_minutes <= 0` em `workout_plan_templates.py` e no editor; `minutos × 60` em `time_estimate` e no espelho; `min={1}` e o padrão `20` no editor | M98 C0 (§69), C5 (§69.7: periodização valida a saída da LLM por `clamp_fields` + `validate`) | `cardio_prescricao_paralela` |
| Defaults de prescrição (`"8-12"`, `"8-9"`, 3/1 séries, descanso 30/90/60, ponto médio; valor inicial do registro da série 10 reps / 20 kg, §57.1) | `domain/prescription_defaults.py` (`default_rest_seconds()`, `midpoint_rest_seconds()`, `MIN_SETS_COUNT`, `SET_LOGGER_FALLBACK_REPS`, `SET_LOGGER_FALLBACK_LOAD_KG`); FE `prescription-defaults.ts` via JSON | literal à mão em gerador, prompt, placeholder, Modo Treino | ✅ E8 (Lote 4c; código morto apagado no 5a; registro da série no B2 da Evolução ZEPP) | 4c | `defaults_prescricao`, `descanso_default`, `fallback_registro_serie`, `fallback_registro_serie_cadeia` |
| Tetos de exercícios/séries por sessão, priorizados, tempo do slot | `periodization/limits.py` (cada teto com nome próprio) | `= <número>` fora do dono; `prioritized_count > 3` | ✅ E9 (Lote 4c) | 4c | `tetos_sessao` |
| Números em prompt de periodização / RAG | interpolados (`methodology_ranges_table`, `template_volume_domain`, `limits.py`, catálogo `techniques`, `prescription_defaults`) | "entre 90 e 110", "pede 4-5 exercicios", "{alvo}-9", "N a M séries semanais" | ✅ E10 (Lote 4c) | 4c | `numeros_prompt_volume` |
| Nome da técnica em prompt de IA (séries do resumo de fim de treino e do resumo de exercício; técnicas vetadas no bloco do objetivo dos quatro prompts do treino) | catálogo `advanced_techniques` (coluna `name`), lido uma vez por `advanced_techniques.technique_display_names` (falha → `{}`), resolvido pelo `technique_code` real (`technique_accounting.row_technique_code`) em `set_labels.set_display_label`; no bloco do objetivo, `goal_prompt_context.goal_section(goals, technique_names)` (código sem nome → menção genérica) | `set_type` ou código cru (`[forced_rep]`, `drop_set`, `rest_pause`) ou mapa código→nome escrito à mão no prompt | ✅ M95 (2026-10-25); M95b/M95c (2026-10-26) | M95, M95b, M95c | — (testes `test_m95_resumo_treino_nome_tecnica.py`, `test_m95c_m95b_nome_tecnica_prompts.py`) |
| Meta semanal de fallback 6/9/27 | `VolumeService.DASHBOARD_FALLBACK_*_SETS` (§4 "Metas") | 27/6/9 reescritos | §4 | — | `meta_fallback` |
| Tolerância de meta (±5%) | `classify_weekly_volume_status` | validação sem tolerância em `activation_projection.py` | ✅ E11 (Lote 4a) | 4a | — |
| Fatia → bloco | `methodology_volume.block_of_group` / `block_volume_class_id` | `markdown_report_service` parando no 1º nó; `size_class` no CRUD | ✅ E12 (Lote 4a; coluna `size_class` fica até a migração de contract) | 4a | — |
| Grupo → grupo pai → macro-grupo por linha, para as telas de volume agruparem (pai = raiz do bloco; macro = classe do bloco, a fatia herda a do pai; sem classe resolvida = balde "Sem macro-grupo"; nome fora do cadastro, como o "Outros", = bloco próprio no balde) | `methodology_volume.resolve_group_hierarchy` (reusa `block_of_group` e `block_volume_class_id`), `index_hierarchy_by_name`/`hierarchy_of_group_name` e `VOLUME_CLASS_UNCLASSIFIED_LABEL`; leitura `methodology_volume_service.load_group_hierarchy` (1 statement, memo por requisição) e `annotate_group_hierarchy`. **Consumidores:** `GET /analytics/muscle-groups-recent-exercise-volume`, `/muscle-groups-equivalent-sets-by-activity`, `/muscle-groups-slot-comparison` (anota as duas fontes em `VolumeService.get_slot_session_comparison`; desde 2026-10-23 é ela que alimenta o T-0..T-3 da tela) e `/training-volume` (só `trend.items`) → `/volume-grupamentos` (`lib/volume-grouping.ts` escolhe a chave e soma); relatório Markdown (`resolve_macro_groups` delega, `MACRO_GROUP_UNCLASSIFIED` importa do dono) | mapa grupo → pai ou grupo → macro no FE; FE lendo `methodology_parent_group_id`/`volume_class_id` para subir a cadeia; o literal do rótulo do balde; agregar status/metas (`volume_status`, `min/max_weekly_sets`) por pai ou macro | Rótulo: as 4 cópias do FE saíram (§50). **Pré-existentes, fora deste lote:** `frontend/src/lib/slot-group-sets.ts` (`resolvePrincipalGroupId`, cópia de `block_of_group` para o editor de Modelo, E5/§18: trabalha sobre o rascunho não salvo) e `frontend/src/lib/volume-classes.ts` (`volumeClassLabelForGroup`, coluna da aba admin: sobe só 1 nível, então numa cadeia de 2+ níveis diverge de `block_volume_class_id`; hoje a árvore tem 1 nível). Saída: a rota de grupos da aba admin devolver o macro-grupo resolvido pelo dono | §50 | `rotulo_sem_macro_grupo` (nasce vazia) |
| Comparativo T-0..T-3 de `/volume-grupamentos` pelo MESMO slot do Modelo (pedido do usuário 2026-10-23): T-0 = sessão mais recente da chave; T-1..T-3 = as anteriores da MESMA chave com a ficha no MESMO slot, até `SLOT_COMPARISON_SESSIONS` = 4; menos sessões no slot = colunas vazias (nunca completadas com outro slot); T-0 sem slot (treino livre, ficha sem Modelo) = só o T-0, status `no_slot`; sessão sem slot nunca entra como anterior. Identidade do slot = `SessionSlot.key` = `workout_plans.template_id` + `template_slot_sequence` (o vínculo da aderência; desde 2026-10-24 o Modelo entra pela RAIZ da linhagem, ver a linha "Linhagem do Modelo"); o `workout_plan_template_slots.id` não serve (cada versão publicada grava ids novos, 065) e o nome é só rótulo. Em grupo pai e macro-grupo, as linhas da sessão na mesma chave somam ANTES da escolha (volume/séries/séries equivalentes somam; RPE ponderado pelas séries); o slot é o do T-0 da chave | `intelligence/slot_session_comparison.py` (`SLOT_COMPARISON_SESSIONS`, `PERIOD_KEYS`, `STATUS_SAME_SLOT`/`STATUS_NO_SLOT`, `SessionSlot`, `select_slot_sessions`, `grouping_key_of`, `build_slot_comparison`); leitura `VolumeService.get_activity_slots` (1 statement, slot da versão por `plan_version_expr`) + `get_slot_session_comparison`. **Consumidor:** `GET /analytics/muscle-groups-slot-comparison` → tabelas e gráficos T-n de `/volume-grupamentos` (cards e "Volume total" leem o nível grupo do mesmo payload) | escolher as sessões T-n no FE (`slice(0, 4)` das sessões); casar slot por nome (`slot_name`) ou por `workout_plan_template_slots.id`; completar coluna com sessão de outro slot | — (antes: o FE pegava as 4 últimas sessões da chave em qualquer slot). `grouping_key_of` repete a chave de `frontend/src/lib/volume-grouping.ts` (`groupingKeyOf`, que segue nas tabelas por semana/equipamento/mapa de calor), travada pela fixture `frontend/src/lib/volume-grouping.parity.json` (lida pelo pytest do dono e pelo vitest). **Impacto medido em 2026-10-23 (produção, usuário 1, só SELECT):** 49 sessões na janela de 400 exercícios, 42 com slot; 100% das linhas mudam de comparação em todos os níveis e nenhuma tem T-1, porque o usuário trocou de Modelo (28 → 34 em 16/10 → 36 em 19/10) e no 36 cada slot tem 1 sessão. Decisão: a chave inclui o Modelo (`template_id`), então trocar de Modelo zera o histórico por slot; só por `sequence` daria T-1 em 12 de 19 grupos, mas casaria o "slot 1" de Modelos diferentes. Limite conhecido: reordenar slots numa versão nova muda a `sequence` (não há identidade de slot estável entre versões) | 2026-10-23 | `slot_por_nome` (allowlist vazia) |
| Grupo de cada sessão na lista de `/dashboard/treinos` (pedido do usuário 2026-10-24: agrupar pela posição no slot da mesma ficha, não pelo nome): chave = `slot_group_key(SessionSlot)` = `slot:<template_id>:<template_slot_sequence>` (desde 2026-10-24, `<template_id>` = raiz da linhagem; ver "Linhagem do Modelo") (a MESMA identidade `SessionSlot.key` do comparativo T-0..T-3); renomear o Modelo ou o título do slot não parte o grupo; rótulo = nome do slot na versão da sessão mais recente do grupo + nome ATUAL do Modelo (`workout_plan_templates.name`). Fallback: ficha sem Modelo ou sem posição (legado anterior ao 026, ficha avulsa) = UM grupo `no_template` ("Fichas sem Modelo"); sessão sem ficha = UM grupo `unlinked` ("Treinos sem ficha"). Nunca por nome nem por `workout_plan_id` | `intelligence/slot_session_comparison.py` (`session_group_of`, `slot_group_key`, `GROUP_KIND_*`, `NO_TEMPLATE_GROUP_*`, `UNLINKED_GROUP_*`); leitura `dashboard/adherence_service._session_payload` (o `SESSIONS_PAGE_SQL`/`SESSION_BY_ID_SQL` trazem `template_name` por `LEFT JOIN workout_plan_templates`). **Consumidor:** `GET /analytics/dashboard/workouts` (`items[].group`, contrato `SessionGroupOut`) → `lib/dashboard-workouts.ts` (`groupSessionsBySlot` só junta por `group.key` e ordena: Modelo treinado mais recentemente primeiro, `sequence` do ciclo, depois sem Modelo e sem ficha) | montar a chave `slot:…` no FE a partir do `plan_link`; agrupar por `activities.name`, `plan_name` ou `workout_plan_id` | antes (commit bdc9eb8): a tela montava a chave a partir do `plan_link` e agrupava ficha sem Modelo por `workout_plan_id` (1 grupo por ficha, rótulos repetidos). **Impacto medido em 2026-10-24 (produção, só SELECT, 88 sessões, todas do usuário 1):** 43 sessões com slot em 25 grupos — chave idêntica à anterior, nenhuma muda de grupo; 18 sessões de ficha sem Modelo saem de 18 grupos de 1 sessão para 1 grupo; 27 sem ficha seguem num grupo. Renomeações: 5 slots do Modelo 36 têm 2 ou 3 nomes entre versões (ex.: "Pull" → "Pull - SEX") e já ficavam juntos; 1 sessão do Modelo 36 tem o nome antigo do Modelo na ficha. Limite resolvido em 2026-10-24: Modelo duplicado (34 → 36) era OUTRO Modelo; agora a cópia grava a origem e conta como ela (linha "Linhagem do Modelo"). Segue: reordenar slots muda a `sequence` | 2026-10-24 | `chave_slot_fora_do_dono` (nasce vazia), `slot_por_nome` |
| Linhagem do Modelo (pedido do usuário 2026-10-24, "junta o 34 com o 36"): a cópia de um Modelo (`POST /workout-plan-templates/{id}/duplicate`) grava a origem em `workout_plan_templates.source_template_id` (090) e conta como o Modelo de origem. A identidade de slot passa a ser `SessionSlot.key` = (Modelo RAIZ da linhagem, `template_slot_sequence`), a MESMA no comparativo T-0..T-3 de `/volume-grupamentos` (o T-1 da cópia pode ser uma sessão do original) e nos grupos de `/dashboard/treinos` (`slot:<raiz>:<sequence>`). Raiz: sobe `cópia → origem`; origem fora do mapa (apagada) = raiz; ciclo = o menor id do ciclo (todos concordam); cadeia além de `MAX_TEMPLATE_LINEAGE_DEPTH` = 32 saltos = o próprio Modelo. Rótulo: nome do slot e do Modelo da sessão MAIS RECENTE do grupo; `template_id` do payload segue sendo o da sessão e `lineage_template_id` diz a raiz (a tela ordena os grupos por ela) | `intelligence/slot_session_comparison.py` (`MAX_TEMPLATE_LINEAGE_DEPTH`, `lineage_root_of`, `resolve_lineage_roots`, `SessionSlot.lineage_template_id`/`lineage_id`/`key`, `session_slot_from_row(row, lineage_roots)`, `session_group_of(..., lineage_roots)`); leitura `intelligence/template_lineage_reader.py` (`TEMPLATE_LINEAGE_SQL`, 1 statement, só ids; `load_lineage_roots`); gravação só em `workout_plan_templates.duplicate_template`. **Consumidores:** `VolumeService.get_activity_slots` (comparativo) e `dashboard/adherence_service._collect_page_details` (1 leitura por página) → `items[].group.lineage_template_id` → `lib/dashboard-workouts.ts` (só ordena por ela) | percorrer a linhagem em SQL (CTE recursiva, JOIN na origem), no FE ou em outro serviço; ler/gravar `source_template_id` fora do leitor e do `duplicate_template`; juntar Modelos por nome ou por grade parecida | antes: cópia = outro Modelo (as 2 sessões do 34 separadas das 5 do 36). **Impacto medido em 2026-10-24 (produção, só SELECT):** 13 Modelos, todos do usuário 1, nenhum com origem gravada. Grade idêntica (v1 da cópia = alguma versão de um Modelo mais antigo do mesmo usuário, mesmos slots e itens): 2 pares, 36←34 e 31←28; só o 36←34 é juntado (091, autorizado pelo usuário); o 31 não tem sessão e fica como está (só reportado). Com 36→34: 43 sessões com slot; 2 sessões (as do 34, slots 2 e 3, 16 e 17/10) mudam de grupo e se juntam às do 36 nas mesmas posições; os grupos com slot passam de 25 para 23; os grupos do 36 mudam de chave (`slot:36:n` → `slot:34:n`) sem mudar de composição; no T-0..T-3 os grupos treinados nos slots 2 e 3 ganham T-1 (antes nenhum grupo tinha). Limite conhecido: cópia editada depois com slots reordenados segue casando pela `sequence` | 2026-10-24 | `linhagem_modelo_fora_do_dono` (nasce vazia), `chave_slot_fora_do_dono` |
| Intensidade × densidade (pontos de RPE 10/9/8 → 1,0/0,7/0,3, faixas de reps 6..15, pico 0..5 s, descanso 180..45 s, séries 2..5, `p = (I − D + 1)/2`, "equilibrado" com \|p − 0,5\| < 0,1, média por grupo ponderada por `contribution.value` nos papéis primário/secundário) | `domain/intensity_density.py` (constantes nomeadas, `compute_item`, `direction_of`, `aggregate_by_group`; rampa por `training_history.parse_rpe_ramp`, reps por `time_estimate.parse_reps_target`, falha por `training_safety.FAILURE_REQUIREMENTS_NEEDING_FAILURE`); espelho `frontend/src/lib/intensity-density.ts` (calcula na tela: o preview não leva RPE, descanso nem pico) lendo a seção `intensity_density` de `motor-constants.generated.json`, com paridade em `intensity-density.parity.json` (gerados por `test_m76_lote1b_espelhos_fe.py --gravar`) | calibração escrita à mão (constante com número, tabela `{ minRpe: 10, … }`, `linear(x, 180, 45)`) fora do dono; segundo parser de rampa no backend; tela recalculando com regra própria | ✅ E13 (2026-10-13, §37: o TS deixou de ser dono; sem mudança de número) | §37 | `intensidade_densidade` (allowlist vazia) |
| Modo do treino pela ficha (§57.3): avançado se algum item tem `advanced_technique`, `warmup_reps` ou `feeler_set_reps` não-nulo (resíduo de técnica removida não conta), senão normal; decidido uma vez ao iniciar a sessão | `frontend/src/lib/workout-mode-advanced-stage.ts` (`exerciseHasAdvancedConfig`, `planHasAdvancedConfig`, `resolveWorkoutModeFromPlan`), no núcleo `workout-mode-core` | `ModeSelectScreen.tsx` (início e retomada sem snapshot), `PlanExecutionScreen.tsx` (só lê `advanced_mode`), relógio (bundle) | disjunção dos três campos fora do dono; escolha "normal/avançado" na tela; `has_advanced_technique=true` (filtro legado do backend, conta pico de contração) no Modo Treino ou no relógio | ✅ ZEPP-B4 | B4 | `modo_treino_pela_ficha` |
| Contabilização de técnica no Modo Treino (pesos além da falha 1,0/1,0/0,5/0,5, códigos ponderados por reps e só-séries, teto padrão 1,0, RPE de esforço cheio 9 e de referência 10, tabela de pontos de esforço) | `domain/technique_accounting.py` (`export_for_frontend`); pontos de esforço de `MuscleRecoveryService._EFFORT_POINTS`/`effort_points` (a contabilização delega); coeficientes POR TÉCNICA do catálogo (`advanced_techniques`, 060) pela API; espelho `frontend/src/lib/technique-accounting.ts` (feedback ao vivo "= 0,5 série equivalente") lendo a seção `technique_accounting` de `motor-constants.generated.json`, com paridade em `technique-accounting.parity.json` | pesos, teto, RPEs de referência e a tabela/clamp de esforço escritos à mão fora dos donos; casos de paridade duplicados à mão entre TS e Python | ✅ 2026-10-13 (§37) | §37 | `pesos_tecnica`, `pontos_esforco` (allowlists vazias) |
| Código morto/legado com regra própria | — (apagar) | `generate_plan_draft`, `abc_workout_*`, `mode_*_legacy`, `template_ai_selector_v2`, `templates_engine` v1 | E14 parte 1 ✅ (Lote 5a: código sem chamador); parte 2 pendente (legado da flag) | 5 (decisão 4) | `dose_reduzida_texto`, `defaults_prescricao`, `descanso_default`, `percentual_de_carga` (zerados no 5a) |
| Grupo muscular por regex no nome do exercício (`MUSCLE_GROUP_CASE`) | — (apagado); a classificação é a canônica (`exercise_muscle_contributions`, lida por `muscle_groups_mapped.py` e `VolumeService`) | `/analytics/muscle-groups`, `-trend`, `-table` | ✅ COD-05/M57 (2026-10-09, §25) | — | — (nunca teve padrão; não há o que reduzir) |
| Medida da série (`reps`/`duration`/`max_reps`, §60): medida ausente = `reps` e nunca inferida do item; só `reps` é "baseada em reps" (progressão, comparabilidade, e1RM, tonelagem, carga da próxima série — motivo `measure_not_rep_based`); limites 1..3600 s (tempo) e 5..600 s (janela), ≥ 1 rep no máximo; forma do item (`duration` exige alvo, sem bloco avançado; `max_reps` janela opcional) e da série (`duration` exige `duration_seconds`, `max_reps` exige `reps`); dose de série por tempo = fallback de reps do e1RM; texto "10 reps" / "60 s" / "15 reps em 30 s" | `domain/set_measures.py` (`SetMeasure`, `DEFAULT_MEASURE`, `MEASURE_LABELS`, `DURATION_SECONDS_*`, `TIME_WINDOW_SECONDS_*`, `MAX_REPS_MIN_REPS`, `normalize_measure`, `is_rep_based`, `sql_is_rep_based`, `sql_if_rep_based`, `sql_duration_seconds`, `has_non_rep_based`, `sql_measure_expr`, `validate_item_measure`, `validate_set_measure`, `dose_reps_for_measure`, `planned_set_execution_seconds`, `format_set_measure`, `REASON_MEASURE_NOT_REP_BASED`, `export_for_frontend()` → bloco `set_measures`); schema na 089; FE **um** `frontend/src/lib/set-measures.ts` lendo o JSON + `set-measures.parity.json` | `measure == 'duration'` / `=== 'max_reps'` / `es.measure = 'duration'` fora do dono; limite de segundos à mão; medida da série deduzida do item da ficha | — (T1: dono, schema e espelho; T2 §60.1: validação do Modelo, versões, ficha, Modo B, prescrição congelada, tempo de treino, Intensidade × Densidade, catálogo; T3 §60.2: gravação da série, `next_set_load`/`reps_position`, agregados/tonelagem, e1RM/recorde/exposição/comparabilidade, progressão, volume/dose, "última vez", relatório) | T1, T2, T3 (T4-T5 consomem) | `medida_serie` (allowlist vazia) |
| Escolha de exercício livre (M101b, pedido corrigido do usuário 2026-10-27; substitui o "equipamento livre" do M101/#654): item do Modo B com o grupo e SEM par (`mapping_id` e `equipment_id` NULL) = "O motor escolhe o exercício"; o equipamento vem por consequência. Na geração: sem academia ou sem grupo → `requires_user_choice`; candidatos = pares exercício+equipamento ATIVOS da academia cujo exercício tem o grupo do item como PRIMARY, sem laboral (M100, `domain/laboral.sql_not_laboral` dentro de `exercise_substitution.load_group_candidates`); fora: exercício já usado em outro item do slot, fixo ou livre (dois livres do mesmo grupo nunca repetem exercício) e técnica fixa incompatível; ordem = ranking único (estrelas do par DESC → recência ASC), com a recência = MAX entre o último treino executado e a última ficha gerada e não executada (`workout_plans.status = 'planned'`, pela `created_at`), para alternar entre gerações seguidas sem treino; teto de segurança explícito = `max_safe_rpe` do candidato contra o RPE-alvo do item (vale o 1º que alcança; nenhum alcança ou sem alvo → o 1º compatível, com o clamp de `training_safety.compose`; nunca piso 0); sem candidato → `requires_user_choice`. Aviso `EXERCISE_AUTO_CHOSEN` e motivo `EXERCISE_FREE_IN_TEMPLATE` (`kind=FREE_EXERCISE`), nunca `EQUIPMENT_SUBSTITUTED`. Congelamento: a linha casa com o item livre por posição + grupo PRIMARY (ou só pelo grupo, na ordem, se o rascunho foi reordenado), sem auditoria de substituição. Volume/ativação PLANEJADOS (M101c): item livre = séries planejadas (`planned_item_sets`, com a técnica) no grupo PRIMARY do item, peso `FREE_EXERCISE_PRIMARY_WEIGHT` = 1,0 (a mesma regra do item `muscle_group` do Modo A), sem SECONDARY/estabilizador, em qualquer modo de contagem; a MESMA conta na prévia do editor, na metodologia de volume (um exercício distinto da sessão: conta em `direct_exercises` e na frequência; deixa de ser `items_without_mapping`) e no planejado/projetado do dashboard. A ficha gerada tem exercício concreto e conta pelo caminho normal; o projetado do Modelo só soma slots pendentes, então não há dupla contagem. O peso do objetivo de treino (`hypertrophy_volume_weight`, M97 F2) segue o de cada superfície, como no item com par: a metodologia aplica, a prévia e o dashboard não (intencional, sem divergência nova). Progressão sem mudança: cada par tem a sua trilha (calibração na 1ª vez aceita). O item "exercício sem equipamento" do #654 saiu: o save o recusa (422) e a geração volta ao D13 (a) | `generation/mode_b._resolve_free_exercise` (+ `REASON_EXERCISE_FREE_IN_TEMPLATE`, `KIND_FREE_EXERCISE`, `REJECT_BELOW_ITEM_TARGET`); candidatos de `generation/exercise_substitution.load_group_candidates`; ordem em `domain/equipment_ranking.py`; leitura `equipment_recommender.load_user_recency(include_planned=True)` / `rank_gym_equipment_rows` (só o ramo livre pede; os demais consumidores seguem só com o executado); aviso `domain/prescription.WARNING_EXERCISE_AUTO_CHOSEN` (também no congelamento, `freeze_mode_b`, casamento em `match_rows_to_model_items`). Validação do save: `workout_plan_templates._validate_template_payload` exige só o grupo e aceita o par completo ou os dois vazios; o que é item livre: `domain/free_exercise_item.is_free_exercise_item` (`require_kind=False` na geração/congelamento) + `FREE_EXERCISE_PRIMARY_WEIGHT`; quanto soma: `intelligence/technique_volume.free_exercise_primary_sets` (prévia `_compute_slot_activation_equipment_series`/`_item_breakdown_source`/fadiga prévia, dashboard `muscle_summary_service.resolve_template_plan` pelo ramo `unpaired` do `TEMPLATE_PLAN_ACTIVATION_SQL`, que só filtra); metodologia `MethodologyItemInput.free_exercise` marcado em `methodology_volume_service._item_input` e somado no motor puro `methodology_volume.evaluate_methodology_volume` com o mesmo peso; o validador da Periodização (`_unusable_reason`) segue exigindo o trio da LLM. **Consumidores:** editor `EquipmentSeriesItemCard.tsx` (Select único "Equipamento — Exercício" + opção "O motor escolhe o exercício"; só grava os dois NULL), `lib/workout-plan-template-form.ts` (validação espelhada, sem número), `lib/activation-preview-payload.ts` (manda o grupo do item livre), `lib/workout-plans-types.ts` (rótulo, tooltip e cor de `EXERCISE_AUTO_CHOSEN`, lidos por `ExercisesTable.tsx`); tela do motor, coluna PLANEJADO por item (M101d): `intelligence/dashboard/engine_service.free_items_by_plan_row` re-deriva o vínculo linha da ficha ↔ item livre chamando o MESMO `freeze_mode_b.match_rows_to_model_items` sobre os MESMOS itens `equipment` do slot (o vínculo não é gravado na ficha; sem migração) e `match_planned` usa o item casado (passo 2, depois do casamento por exercício; cada item casa uma vez só) com `free_exercise: true` no payload; o FE (`lib/dashboard-engine.plannedSummary`) só troca o rótulo para "<grupo> (exercício escolhido pelo motor)"; Modelo sem item livre não faz statement a mais; status "gerada e não executada" importado de `workout_plan_edit_rules.PLANNED_PLAN_STATUS` | ordenar candidatos ou escolher o exercício livre no FE, em SQL ou em outro motor; ler a recência das fichas planejadas fora do dono; usar `EQUIPMENT_SUBSTITUTED` para o item livre | — (novo). Motor legado (`mode_b_legacy`, só rollback da flag) não ganhou o ramo. Limite conhecido da tela do motor (M101d): o casamento é re-derivado na leitura, então um rascunho editado a mão depois da geração (linha trocada para exercício de outro grupo) pode deixar o livre sem linha, e ele segue no orçamento do grupo, como antes. Limite conhecido: `workout_plans.created_at` (TIMESTAMP do servidor) e `activities.ended_at/started_at` podem estar em relógios diferentes; o efeito é só no desempate de recência | M101, M101b, M101c, M101d | `recencia_planejada_fora_do_dono`, `exercicio_livre_fora_do_dono` (allowlists vazias) |

### 3.6 Contexto da sessão (ordem dos exercícios)

| Regra | Dono | Consumidores permitidos | Proibido fora do dono | Divergências | Lote | Guarda |
|---|---|---|---|---|---|---|
| Fadiga prévia na sessão (músculo principal, séries equivalentes prévias, nível 0..1, faixa descansado / pré-fatigado / muito fatigado) | `domain/prior_fatigue.py` (`prior_fatigue_for_sequence`, `principal_muscle`, `level_for`, `band_for`, `PRIOR_FATIGUE_PRE_FATIGUED_MIN_SETS` = 2, `PRIOR_FATIGUE_HEAVILY_FATIGUED_MIN_SETS` = 6, `PRIOR_FATIGUE_SATURATION_SETS` = 9, `PRIOR_FATIGUE_BAND_LABELS`); entradas: séries de `technique_volume.planned_item_sets` (Modelo) ou séries de trabalho executadas (sessão), pesos `stimulus_weight` do dono de "Pesos por papel" | editor de Modelo: preview `item_prior_fatigue[]` (`workout_plan_templates._item_prior_fatigue`) → faixa de cor dos cards (`frontend/src/lib/prior-fatigue.ts` só converte `level` em cor e usa `band_label` da API). **PR 2 (§22)**: `prior_fatigue.context_band` (faixa = contexto; sem músculo principal = descansado) e `band_label`; montagem das entradas da sessão REAL e PLANEJADA em `domain/session_fatigue.py` (séries não preparatórias × `realized_set_units`, ordem `exercises.sequence`; planejadas por `planned_item_sets`), leitura em lote em `intelligence/session_fatigue_reader.py` (contribuições pelo resolvedor `resolve_muscle_contributions_with_cursor`) → comparabilidade (`comparability.py`/`exposure_service.py`), trilha/avaliador (`progression_state.load_fatigue_contexts`) e prescrição (`deterministic_prescription.planned_fatigue_contexts`) | cortes/saturação com número fora do dono; comparar `level`/`prior_sets` com número no FE; soma própria de séries anteriores × peso | — (dono nasce sem cópias) | F1 (2026-10-07) | `fadiga_previa` (allowlist vazia) |
| Indicador de fadiga do grupo no Modo Treino (M79, 2026-10-11): mesma conta do contexto, mas com `fatigue_weight`, por SÉRIE e em 4 faixas Descansado / Pré-fatigado / Fatigado / Muito fatigado. A leitura de contexto da linha acima **não muda**: continua com `stimulus_weight`, 3 faixas e por exercício, e segue sendo a chave `fatigue_context`. | `domain/prior_fatigue.py` (`group_fatigue_for_sequence`, `group_band_for` = `band_for` + `GROUP_FATIGUE_FATIGUED_MIN_SETS` = 4 dentro de pré-fatigado, `level_for`, `principal_muscle` por `stimulus_weight`, `fatigue_weight_of`: `fatigue_weight` cadastrado; ausente → `stimulus_weight` da mesma contribuição, `GroupFatigueBand`, `GROUP_FATIGUE_BAND_LABELS`). Entradas: unidade de cada série executada (`session_fatigue.executed_set_units` = `realized_set_units`), na ordem `exercises.sequence`; pesos do resolvedor (`contribution_from_mapping` passa a ler `fatigue_weight`) | `GET /exercises/last-session-sets`: `item.sets[].group_fatigue` (antes de cada série da sessão anterior, `session_fatigue_reader.load_set_group_fatigue`) e `current_group_fatigue` ("Fadiga atual do grupo": sessão em andamento antes da 1ª série do exercício, `session_fatigue.group_fatigue_before_exercise`). O FE (`workout-mode-previous-session.ts`) só concatena `band_label` | cortes com número fora do dono; FE comparar `group_fatigue.prior_sets`/`level` com número ou somar pesos; usar `fatigue_weight` no contexto/comparabilidade | — (nasce sem cópias) | M79 (2026-10-11) | `fadiga_previa` (padrão estendido a `GROUP_FATIGUE*`/`group_fatigue`; allowlist vazia) |

### 3.7 Nutrição

Dono: o pacote `domain/nutrition/` (Lote L1, §45), Python puro, com `ENGINE_VERSION = "nutrition-v1"`.
Plano: [`docs/nutricao/PLANO_NUTRICAO_2026-10-14.md`](../nutricao/PLANO_NUTRICAO_2026-10-14.md) §3 e §7.
O orquestrador `daily_target.compute_daily_target` é o único ponto que a api-core chama para
materializar a meta do dia (E2). Os demais consumidores (L2+) chamam o dono de cada linha.
**Consumidores em runtime**: desde o L2a (§46), `users.py` e `auth.py`, que validam, gravam e devolvem o
perfil antropométrico por `anthropometrics`; desde o L2b (§47), `api/app/nutrition_targets_service.py`
(materialização) e `api/app/nutrition_plans.py` (rotas do plano). A tela `/nutricao/plano` lê o bloco
`nutrition` do JSON gerado e recebe todos os números prontos da API. Desde o L3a (§48),
`api/app/nutrition_foods.py` (catálogo: busca por `food_text`, entrada por `catalog`), `api/app/nutrition_access.py`
(quem vê, edita, promove e cria porção de um alimento, D-G) e a migração 081 (seed da TACO com o
`name_normalized` do motor e a citação de `catalog`, conferidos por teste); a tela `/nutricao/alimentos` lê o
bloco `nutrition` (campos da RDC 429, fontes, base, citações) e recebe nota, ordem e permissões prontas. Desde o
L3b (§49), `api/app/nutrition_external_foods.py` (código de barras pelo Open Food Facts e busca/importação do USDA
FoodData Central: GTIN, limite diário, mapeamento de nutrientes, energia, citação e licença por
`external_sources`/`catalog`) e o cliente HTTP `api/app/external_food_sources.py` (só transporte: timeout, chave no
cabeçalho, erros 502/503/504/404); os componentes `BarcodeLookup` e `UsdaSearch` da mesma tela leem do JSON os
tipos de dado do USDA, as citações e as licenças (`food_licenses`, rodapé da ODbL) e recebem os valores prontos.
Desde o L4 (§52), `api/app/nutrition_meals.py` (refeições: quantidade na base, macro ausente, nutrientes congelados por
item e totais por `meal_math`; entrada por `diary`), `api/app/nutrition_days.py` (dia e semana: meta do dia pela
decisão do motor, totais, balanço, banco e aderência por `weekly_budget`, kcal do treino "informado × estimado"
por `energy_expenditure.compare_training_kcal` com a proveniência de `domain/calories_source`),
`nutrition_targets_service.ensure_diary_targets` (meta retroativa gravada uma vez, R8) e
`nutrition_access.can_log_food`/`can_log_portion`; a tela `/nutricao` (diário) lê o bloco `nutrition` (unidades,
porções, nutrientes, escala de fome, faixas) e recebe todos os números prontos (preview pela API, E9).
Desde o L5a (§54), `api/app/nutrition_ai_quota.py` (uso e cota de IA no `ai_usage_log`: reserva pela cota de
`ai_usage` no dia do usuário de `time_windows.user_day_start_db_for`, fechamento pelo worker com o custo de `ai_usage` e sweep pelo prazo
do worker perdido do M73 em `async_jobs`); sem rota, tela nem IA até o L5b (§55) e o L6 (§56), que só chamam as
funções dele. Desde o L6 (§56), `api/app/nutrition_label_photo.py` (upload da foto do rótulo: limite e tipo de
`label_photo`, cota pelo `nutrition_ai_quota`, job `ai_usage.LABEL_PHOTO_JOB_TYPE` na `musclefit-plan-generator`),
`api/app/nutrition_label_storage.py` (S3 na chave de `label_photo_key`) e o worker `api/app/nutrition_label_ai.py`
(prompt que interpola `labels.RDC429_FIELDS`, `LABEL_REGULATION_REFERENCE` e `PER_100G_REFERENCE`, sem conta nem
conferência; a IA só transcreve e `label_photo.review_reading` decide); o corpo do upload é JSON com a foto em base64
sem padding (`label_photo.decode_upload`, por causa do WAF); o cadastro aceita
`source=label_photo` pelo `catalog.USER_ENTRY_SOURCES`; a tela `/nutricao/alimentos` reduz a foto por canvas com os
parâmetros do bloco `nutrition.label_photo` do JSON gerado (`frontend/src/lib/image-resize.ts`, sem default numérico) e
recebe o formulário pré-preenchido pronto.

| Regra | Dono | Consumidores permitidos (L2+) | Proibido fora do dono | Lote | Guarda |
|---|---|---|---|---|---|
| Sexo canônico, idade, IMC, nível de atividade e fatores (clássicos 1,2–1,9; de não-exercício 1,2–1,6, D-A), faixa de altura; faixa de sanidade do peso informado em formulário (`WEIGHT_KG_RANGE`, L2b, default de desenho) | `nutrition/anthropometrics.py` (`Sex`, `normalize_sex`, `ActivityLevel`, `normalize_activity_level`, `ACTIVITY_FACTORS`, `NON_EXERCISE_ACTIVITY_FACTORS`, `HEIGHT_CM_RANGE`, `WEIGHT_KG_RANGE`, `age_years`, `bmi`, `Subject`: contrato público estável para o L0 e o L2) | `users.py` (validação de `sex`/`height_cm`/`activity_level`, L0/L2a), materialização (L2b, via `subject`), plano (peso-alvo, via `plan`), FE via JSON | fator 1,375/1,55/1,725 ou tabela de fatores reescrita; normalizar sexo por conta própria | L1 | `fator_atividade` |
| Perfil antropométrico do usuário: sexo gravado e devolvido SEMPRE no código canônico (`male`/`female`/`other`, apelidos antigos como 'M'/'F' aceitos na entrada e normalizados na leitura), altura dentro de `HEIGHT_CM_RANGE`, nível de atividade no código do motor; 400 com o código do motor (`invalid_sex`, `invalid_activity_level`, `invalid_height_cm`); opções e rótulos das telas | `nutrition/anthropometrics.py` (`sex_code`, `activity_level_code`, `height_is_valid`, `HEIGHT_CM_RANGE`, `INVALID_*`) + `nutrition/labels.py` (`SEX_LABELS`, `ACTIVITY_LEVEL_LABELS`) → bloco `nutrition` do JSON gerado → `nutrition-constants.ts` (`SEX_OPTIONS`, `ACTIVITY_LEVEL_OPTIONS`, `NUTRITION_FORM_RANGES.height_cm`) | `PUT /users/me`, `PUT /users/{id}`, `POST /users` e as leituras `GET /users(/me)`/`/auth/me` (`users.py`, `auth.py`); telas `perfil/PerfilClient.tsx` e `usuarios/UsuariosClient.tsx`; migração 077 (cópia congelada dos apelidos, paridade em `test_migration_077_plano.py`) | opção/código/rótulo de sexo escrito à mão (`value: 'M'`, `sex === 'F'`, `'Masculino'`); faixa de altura ou lista de níveis à mão no FE ou na rota; gravar o texto recebido sem passar por `sex_code` | L2a | `vocabulario_sexo`, `nivel_atividade_e_altura` |
| TMB (Mifflin-St Jeor, Katch-McArdle, Cunningham, Harris-Benedict só comparação) e escolha `auto` (MLG ≤ 60 d → Katch; senão Mifflin; `OTHER` sem MLG → None) | `nutrition/bmr.py` (`bmr`, `select_formula`, `compare_with_scale`, `FFM_FRESHNESS_MAX_DAYS`, `SCALE_BMR_DEVIATION_WARN_SHARE`) | `daily_target`; `body_composition.bmr_kcal` da balança só é comparado, nunca é dono | coeficiente de fórmula escrito à mão (370 + 21,6, 500 + 22, 6,25·cm, 88,362/447,593) | L1 | `tmb_formula` |
| kcal do treino por NATUREZA: informada (`activities.calories_kcal`, `DataNature.OBSERVED`, `DataQuality.HIGH`) > MET × 3,5 × kg / 200 × min (Compendium 2024, `ESTIMATED`/`LOW`, calculada na leitura, nunca gravada, E6); **sem Keytel** (D-K). TDEE explícito (fator de não-exercício + treino do dia) ou embutido (fator clássico). Linha "informado × estimado" do diário (L4): as duas naturezas lado a lado e a escolhida pela prioridade (`compare_training_kcal`) | `nutrition/energy_expenditure.py` (`TrainingKcalNature`, `TRAINING_KCAL_NATURE_PRIORITY`, `resolve_training_kcal`, `compare_training_kcal`, `MET_*`, `tdee`) | `daily_target`; `GET /workout-sessions/{id}` (`calories_estimate`, L0/L2); `GET /nutrition/days/{date}` (`nutrition_days.py`, L4, com a proveniência de `domain/calories_source`); dashboard (L7) | `calories_kcal or/??/\|\| <estimativa>` fora do dono; MET ou `/ 200` escrito à mão; vocabulário de PROVENIÊNCIA (manual × Intervals) neste pacote — esse é de `domain/calories_source.py` (L0); enum próprio de natureza/qualidade (o vocabulário é `domain/data_nature.py`, §3.1) | L1 | `kcal_treino_prioridade`, `met_compendium` |
| %G pela Marinha dos EUA, MLG, FFMI (normalizado 6,1·(1,8 − alt)), cintura/altura < 0,5, peso-alvo, MÉTODO da leitura com prioridade bioimpedância > circunferência | `nutrition/body_composition.py` (`NAVY_*`, `FFMI_*`, `WHTR_HEALTHY_MAX`, `BodyFatMethod`, `BODY_FAT_METHOD_PRIORITY`, `BODY_FAT_RANGE_VALID`) | materialização (L2), dashboard Corpo/Nutrição (L7) | coeficientes da Marinha e do FFMI escritos à mão | L1 | `navy_gordura`, `ffmi_normalizado` |
| Densidade energética da variação de peso (Hall 2008: gordura 9.400, magro 1.800 kcal/kg; Forbes MG/(MG + 10,4)); `DAYS_PER_WEEK` do pacote | `nutrition/energy_density.py` | `strategy` (ritmo do cutting), `adaptive_tdee` | "7.700 kcal/kg" fixo; 9.400/1.800 escritos à mão | L1 | `densidade_energetica` |
| Estratégias (cutting/bulking/manutenção): faixas, defaults e alvo calórico com os clamps do cutting (déficit ≤ 25% do TDEE, ingestão ≥ 1,0 × TMB, D-B) | `nutrition/strategy.py` (`StrategyParams.defaults_for`, `kcal_target`, `CUT_*`/`BULK_*`/`MAINT_*`) | plano admin/PT (L2), formulário via JSON (`form_ranges`) | faixa/default em g/kg ou % escrito à mão (código, tela, prompt, RAG) | L1 | `faixa_proteina` |
| Macros: Atwater 4/4/9/7, fibra 14 g/1.000 kcal, divisão em gramas, proteína por refeição 0,40–0,55 g/kg, 3–6 refeições | `nutrition/macros.py` (`kcal_from_macros`, `macro_split`, `KCAL_PER_G_*`) | `meal_math`, `carb_cycling`, `daily_target`, diário (L4) | `* 4`/`/ 9` ou `KCAL_PER_G_* = <número>` fora do dono | L1 | `kcal_por_grama`, `faixa_proteina` |
| Ciclo de carbo: dia de treino ×1,25, descanso ×0,75 reescalados para a soma semanal = 7 × alvo, piso 1 g/kg, proteína e gordura constantes, kcal acompanha o carbo (D-I) | `nutrition/carb_cycling.py` (`day_kinds_for_week`, `week_multipliers`, `distribute_week`, `apply_cycle`) | `daily_target`; materialização com os dias de treino do Modelo (L8) | multiplicador ou piso reescritos; ciclar fora do dono | L1 | — (sem literal estável; invariantes em `test_nutrition_carb_cycling.py`) |
| Calendário de fases: MATADOR 2/2, ICECAP 3/1, refeed 2 dias/semana, teto de 8 semanas de cutting sem pausa, %G de parada (H 15/10, M 25/18, D-C; o efetivo é o do plano ou o default, `resolve_stop_body_fat`, L2b); pausa/refeed levam a meta à manutenção | `nutrition/phases.py` (`propose_calendar`, `phase_for_date`, `refeed_dates`, `stop_body_fat_default`, `resolve_stop_body_fat`, `should_stop`) | publish do plano (L2/L8), `plan.goal_summary` (L2b), `context_service` (fase derivada, L8) | protocolo escrito à mão ("2 on/2 off", "3+1"); fallback do %G de parada na rota ou na tela | L1 | `pausa_dieta` |
| TDEE observado (adaptativo): ingestão média − inclinação do peso (mínimos quadrados) × densidade; só com ≥ 14 dias, ≥ 80% registrados e pesagens cobrindo ≥ 1 semana; `reconcile` só SUGERE (D-J) | `nutrition/adaptive_tdee.py` (`observed_tdee`, `weight_trend`, `reconcile`, `RECONCILE_WEIGHT_BY_QUALITY`) | dashboard (L7), sugestão de recálculo ao admin/PT (L8) | recálculo automático do plano; tendência de peso própria para o gasto | L1 | — (limiares cobertos por `test_nutrition_adaptive_tdee.py`) |
| Semana: começa na segunda, orçamento = soma das metas (E3), banco ≤ 10% do alvo diário e ≤ 5% do semanal, aderência ±5% nas kcal e proteína ≥ 90%, semana válida com ≥ 5 dias (D-H). Diário (L4, §52): dia "registrado" só com refeição e energia + 3 macros COMPLETOS (`intake_macros`; dia de total incompleto fica fora do banco e da aderência, `intake_incomplete_days`), balanço do dia por nutriente (`day_balance`: meta − ingestão e fração da meta só com ingestão completa; ingestão − TDEE), relatório da semana (`week_report`) e a correspondência nutriente do diário → campo da meta (`BALANCE_FIELDS`) | `nutrition/weekly_budget.py` (`week_start`, `weekly_budget`, `bank_state`, `weekly_adherence`, `target_macros`, `intake_macros`, `day_balance`, `week_report`) | `GET /nutrition/weeks` e `GET /nutrition/days/{date}` (`nutrition_days.py`, L4), dashboard (L7) | tolerância/teto reescritos; SQL decidindo aderência; saldo ou fração da meta calculados na tela; dia incompleto somado como se fosse completo | L1, L4 | `diario_nutricao`, `soma_nutrientes` |
| Diário (L4, §52): refeição (`MealSlot`, ordem do dia `meal_sort_key`), origem (`MealSource`), entrada da refeição (slot, 1 a `MEAL_MAX_ITEMS` = 30 itens, default de desenho) e do item (converte para a base: senão `unit_without_conversion`/`missing_quantity`; até `ITEM_AMOUNT_MAX` = `catalog.PORTION_AMOUNT_MAX`), água (`WATER_ML_RANGE` 0–10.000 ml, default de desenho) e fome (`HUNGER_SCALE` 1–5) | `nutrition/diary.py` (`normalize_meal_slot`, `validate_meal_header`, `item_amount_errors`, `validate_day_log`, `hunger_levels`) + `labels.py` (`HUNGER_LABELS`, `UNIT_LABELS`) | `nutrition_meals.py`, `nutrition_days.py` (400 com os códigos), FE via JSON (`unit_labels`, `portion_units`, `hunger_*`, `water_ml_range`, `meal_max_items`, `nutrient_fields`) | slot, teto, faixa de água/fome ou ordem das refeições decididos na rota, no Pydantic, no SQL ou na tela | L4 | `diario_nutricao` |
| Matemática da refeição: gramas por unidade/porção/medida caseira (colher de sopa 15 ml, sobremesa 10, chá 5, xícara 240), macros por 100 g, natureza dos totais `MealNature` (DERIVED/ESTIMATED = valores de `DataNature`; MIXED é só da refeição), Atwater 15% e rótulo 10% (defaults de desenho, sem fonte; a RDC 429 não fixa tolerância) | `nutrition/meal_math.py` (`GENERIC_PORTIONS`, `MealNature`, `resolve_grams`, `item_macros`, `meal_nature`, `atwater_check`, `label_consistency`; L4: `NUTRIENT_FIELDS`, `Nutrients`, `complete_missing_nutrients`, `PORTION_UNITS`, `portion_amount_for`, `resolve_amount`, `item_nutrition`, `NutritionTotals`, `sum_items`, `combine_totals`, `totals_from_stored`) | `catalog.validate_food_values` (Atwater do alimento cadastrado, L3a), `POST /nutrition/meals(/preview)` (`nutrition_meals.py`, L4, E9: o FE não calcula), totais do dia e da semana (`nutrition_days.py`, L4), worker do rótulo (L6) | macros de item recalculadas no FE ou em SQL; porção caseira escrita à mão; macro ausente somado como 0 (ou inferido) fora do dono; total que esconde o campo incompleto | L1, L4 | `kcal_por_grama` (Atwater via `macros`), `kj_kcal` (kJ ↔ kcal, L3b), `diario_nutricao`, `soma_nutrientes` |
| Texto de alimento: normalização (sinônimos pt-BR, gênero/número do preparo, qualificador implícito, `NOISE_TOKENS`; "sem" não é stopword), nota 0..1 com cobertura da busca (`QUERY_COVERAGE_WEIGHT`), candidato ≥ 0,6, pré-seleção ≥ 0,85 só do primeiro e com folga `MATCH_AUTO_ACCEPT_MIN_MARGIN` sobre o segundo; busca do catálogo: termos para o FULLTEXT (`search_terms`, ≥ `FULLTEXT_MIN_TOKEN_CHARS`), expressão booleana (`fulltext_boolean_query`: `termo*` em OU) e prefixo do termo curto (`prefix_pattern`), lote de candidatos (`SEARCH_CANDIDATE_POOL`), ordem (`order_for_search`) e limite (`search_limit`) — calibrado na TACO no L3a (§48) | `nutrition/food_text.py` (`normalize_name`, `score`, `rank_candidates`, `order_for_search`, `search_terms`, `fulltext_boolean_query`, `prefix_pattern`, `search_limit`, `SYNONYMS`) | `GET /nutrition/foods/search` (`nutrition_foods.py`, L3a), `name_normalized` do seed da 081 (embutido; teste de paridade) e do cadastro, worker do parse (L5) | outro matching/limiar/ordem fora do dono; busca que normaliza ou monta termos à mão; limiar ou lote redefinidos com número | L1, L3a | `catalogo_alimentos`, `nota_matching` (0,85 em Python é de `limiar_fadiga`; aqui é `1 − 0,15`) |
| Catálogo de alimentos (L3a, §48): fontes (`FoodSource`), fontes somente leitura (`READ_ONLY_SOURCES`: TACO; USDA e OFF desde o L3b, §49), citação e licença por fonte (R4; `SOURCE_CITATIONS`/`SOURCE_LICENSES`), obrigatórios da importação externa (`EXTERNAL_REQUIRED_PER_100_FIELDS`, L3b), base g/ml (`FOOD_BASES`, códigos de `meal_math.Unit`), campos por 100 e da porção com os códigos da RDC 429, obrigatórios do cadastro, regras de entrada (número ≥ 0, componente em g ≤ massa da base, sódio em mg idem, energia ≤ 9 kcal/g da base; base ml com `ML_BASIS_MAX_DENSITY_G_PER_ML`, default de desenho) e avisos (Atwater, subtotal > total, macros > base) e faixa da porção (`PORTION_AMOUNT_MAX`, default de desenho); proveniência por campo (M85, §48.1: valor da linha que veio de outra fonte — hoje os `*` da TACO com o USDA —, formato do JSON de `food_items.field_sources_json`, motivo `FieldSourceReason`) | `nutrition/catalog.py` (`validate_food_values(required=)`, `validate_portion_amount`, `is_read_only`, `citation_for`, `license_for`, `SOURCE_CITATIONS`, `SOURCE_LICENSES`, `PER_100_FIELDS`, `FieldSource`, `parse_field_sources`, `field_sources_json`, `field_sources_snapshot`) + `nutrition/labels.py` (`field_source_note`, `field_source_detail`, `field_sources_public`, `FIELD_SOURCE_REASON_LABELS`) | `nutrition_foods.py` (400 com os códigos, `warnings`; `field_sources` com a nota), `nutrition_meals.py` (snapshot do item leva a origem), `nutrition_access.py` (somente leitura; visibilidade D-G de alimentos e porções: `can_*_food`, `foods_visibility`, `portions_visibility`, `ownership`), migrações 081 (citação/licença/versão embutidas; teste de paridade) e 084 (JSON gerado por `field_sources_json` no gerador `scripts/gerar_taco_usda_reavaliacao.py`), FE via JSON (`food_*`) e a nota pronta da API (`FieldSourcesNote`) | faixa, obrigatório, fonte somente leitura ou citação decididos na rota, no Pydantic, no SQL ou na tela; nota de origem escrita à mão na tela ou JSON de proveniência montado fora do dono | L3a, M85 | `catalogo_alimentos` |
| Fontes externas do catálogo (L3b, §49): USDA só Foundation e SR Legacy (`USDA_DATA_TYPES`), ids de nutriente do FDC com a unidade esperada (`USDA_NUTRIENT_IDS`), energia 1008 > 2048 > 2047 > 1062 (kJ → kcal por `meal_math.kj_to_kcal`); OFF pela API v3.6 (`nutrition.aggregated_set`, só `as_sold` e `per` 100g/100ml → base g/ml), chaves do OFF (`OFF_NUTRIENT_KEYS`), sódio g → mg (`catalog.MG_PER_G`), valor `estimate` ignorado, energia declarada > `energy-kj` > `energy` (o `value_computed` do OFF nunca), nome em português primeiro; ausente = None (nunca 0); validação do catálogo com só a energia obrigatória (`catalog.EXTERNAL_REQUIRED_PER_100_FIELDS`; erro recusa com 422, Atwater/subtotal viram aviso); citação/licença por fonte e por linha (USDA CC0; OFF: aviso da ODbL + link do produto; `catalog.USDA_*`/`OFF_*`); USDA e OFF somente leitura; GTIN-8/12/13/14 com dígito verificador e forma canônica (`normalize_gtin`); limite de consultas novas ao OFF por usuário e dia (`OFF_LOOKUPS_PER_USER_DAY` = 30, `off_lookup_allowed`); página da busca do USDA (`USDA_SEARCH_PAGE_SIZE` = 25, default de desenho) | `nutrition/external_sources.py` (`map_usda_food`, `usda_search_request`, `usda_search_result`, `usda_search_items`, `map_off_product`, `normalize_gtin` com `GS1_CHECK_WEIGHTS`/`GS1_MODULUS`, `off_lookup_allowed`, `OFF_LOOKUP_COUNTED_SOURCE`, `OFF_PRODUCT_FIELDS`) + `nutrition/catalog.py` (citações, licenças, `READ_ONLY_SOURCES`, `EXTERNAL_REQUIRED_PER_100_FIELDS`) + `nutrition/labels.py` (`USDA_DATA_TYPE_LABELS`, textos de `invalid_barcode`, `off_daily_limit`, `usda_data_type_not_supported`) | `nutrition_external_foods.py` (rotas: só SQL, contagem do dia pelo fuso do usuário em `time_windows`, resposta), `external_food_sources.py` (cliente HTTP: pede ao OFF só `OFF_PRODUCT_FIELDS`, à busca do USDA só os tipos e a página do motor), migração 084 (M85: valores gerados por `map_usda_food` no gerador `scripts/gerar_taco_usda_reavaliacao.py`; paridade em `test_nutrition_taco_usda_reavaliacao.py`), FE via JSON (`usda_data_types`, `food_citations`, `food_licenses`) | ler o payload cru fora do dono (`foodNutrients`, `nutrientId`, `aggregated_set`, `nutriments`, `value_computed`, `totalHits`, id 1003/1008…, chave `energy-kcal`/`*_100g`); montar o corpo da busca do USDA (`usda_search_request`) ou filtrar tipo com literal; converter kJ/sódio, escolher energia, validar EAN ou comparar com 30 na rota, no cliente ou na tela | L3b | `fontes_externas_alimentos`, `kj_kcal` |
| Rótulos pt-BR (estratégia, fase, tipo de dia, refeição, nível, fórmula, natureza da kcal do treino e dos totais da refeição, método de %G, fonte e base do alimento…) e campos da RDC 429 (`RDC429_FIELD_LABELS` com a unidade, para os códigos `<erro>:<campo>` do catálogo). Natureza/qualidade COMUNS não entram (§3.1) | `nutrition/labels.py` → `export_for_frontend()` → bloco `nutrition` de `motor-constants.generated.json` → `frontend/src/lib/nutrition-constants.ts` (paridade em `nutrition-constants.parity.test.ts`) | telas (L2+; `/nutricao/alimentos` desde o L3a), prompts (L5/L6 interpolam `RDC429_FIELDS`, `GENERIC_PORTIONS`, `MEAL_SLOT_LABELS`) | rótulo escrito à mão no FE ou no prompt | L1 | `rotulo_estrategia` |
| Meta diária (TMB → treino → TDEE → alvo → macros → ciclo/fase), `ENGINE_VERSION` | `nutrition/daily_target.py` (`compute_daily_target`, `PlanParams`, `DayContext`, `DailyTarget`) | `nutrition_targets_service.ensure_daily_targets` (L2b, materializa e congela, E2), preview e publicação de `nutrition_plans.py` (L2b) | encadear os donos à mão fora do orquestrador | L1 | — |
| Materialização (L2b, §47): congelamento `TARGET_FREEZE_LAG_DAYS` = 2 (o sync do Intervals grava a kcal do treino na madrugada seguinte; `is_target_frozen` no "hoje" do fuso do aluno) e `first_recomputable_day` (o mesmo critério); janelas `TargetWindow` (`week_window`; `publication_window`: cria até o fim da semana e ATUALIZA toda meta já materializada de hoje em diante; `recompute_window`: a partir do primeiro dia recalculável, fim padrão no fim da semana, erros `date_from_frozen`/`date_to_before_date_from`/`range_too_long`); teto `TARGET_RECOMPUTE_MAX_DAYS` = 31 (default de desenho) só para CRIAR (`range_within_cap`, `days_in_range`); `days_to_materialize` (nunca dia congelado; meta nova só até `create_until`); contexto do dia pelas sessões concluídas (`day_context_for`: soma da kcal informada só com todas as sessões com kcal, senão MET pela soma das durações; dia futuro sem treino conhecido, `training_unknown_future_day`, decisão GRAVADA em `nutrition_daily_targets.training_known`); dados do sujeito que faltam (`missing_subject_data`, o 422 do preview). Diário (L4, §52, risco R8): decisão por dia (`diary_target_action`: não congelado materializa, congelado com meta só lê, congelado SEM meta é calculado e gravado UMA vez, futuro além da semana sem meta não cria), janela do diário (`diary_window`) e a marca da meta retroativa (`LIMIT_TARGET_BACKFILLED` nas limitações, `backfill_marker` nas entradas, `was_backfilled`) | `nutrition/daily_target.py` | `nutrition_targets_service.py` (lê `training_known` da coluna; `ensure_diary_targets`), `nutrition_plans.py`, `nutrition_days.py` (L4) | dia congelado, janela, teto ou "treino conhecido" decididos fora do dono (comparar `local_date` com hoje à mão, `+ 1` de intervalo, lag escrito à mão); somar kcal de sessões ou escolher informada × MET na rota/SQL; lista de "dados faltantes" montada na rota; regravar meta congelada ou decidir a retroativa na rota | L2b, L4 | `relogio_sem_fuso` (os três módulos da api-core em `MODULOS_DE_MOTOR`), `diario_nutricao` (lag) |
| Plano do aluno (L2b, §47): defaults da estratégia nos campos ausentes (`bmr.DEFAULT_BMR_FORMULA` para a TMB), validação nas faixas da fonte que BLOQUEIA a entrada (400 com `param_out_of_range:<campo>`, `invalid_code:<campo>`, `param_not_applicable:<campo>`; campos de uma estratégia só em `STRATEGY_ONLY_FIELDS`), ciclo de carbo recusado até o L8 (`carb_cycling_unavailable`), estado rascunho/vigente/substituído, plano em vigor por dia (maior `effective_from <= dia`, desempate pela publicação; o `GET /me/plan` usa o mesmo) e meta de composição (`goal_summary`: %G alvo efetivo e se é default, peso para a MLG atual, se a fase já deveria parar) | `nutrition/plan.py` (`build_plan_settings`, `PlanSettings`, `PlanStatus`, `PlanState`, `plan_state`, `plan_in_force`, `STRATEGY_ONLY_FIELDS`, `goal_summary`) | `nutrition_plans.py` (entrada, leitura e vigência do plano), `nutrition_targets_service.py` (vigência por dia), FE via JSON (`plan_states`, `form_ranges.<estratégia>.protein_basis`, `default_bmr_formula`, `strategy_only_fields`) | faixa/default do formulário escrito na rota, no Pydantic ou na tela; vigência por dia decidida em SQL (`superseded_at IS NULL` só trava a vigente na publicação); campo da estratégia decidido no FE pela presença de faixa | L2b | `faixa_proteina` (as chaves do formulário com número) |
| Sujeito do dia (L2b, §47): seleção POR GRANDEZA (`SubjectSeries.readings_on`): para peso, %G e MLG da bioimpedância, TMB da balança, pescoço, cintura e quadril, a leitura VÁLIDA mais recente até o dia, independente das outras (positiva; %G também em `BODY_FAT_RANGE_VALID`), então um %G inválido não esconde MLG/TMB válidas anteriores; sexo pelo `normalize_sex` (aceita o `M`/`F` do perfil antigo); %G da bioimpedância antes da Marinha (`NAVY_SITE_FIELDS`); MLG informada da bioimpedância; idade da MLG pela leitura usada (na Marinha, a medida mais antiga); nível de atividade do plano antes do perfil | `nutrition/subject.py` (`SubjectSeries`, `latest_valid`, `SubjectReadings`, `Reading`, `resolve_subject`, `ResolvedSubject`, `NAVY_SITE_FIELDS`) | `nutrition_targets_service.py` (só preenche as séries cruas em SQL, histórico até o fim da janela) | escolher a leitura do dia, bioimpedância × fita, combinar linhas ou calcular idade/idade da MLG fora do dono (inclusive em SQL, com `ROW_NUMBER`/`LIMIT 1` por linha) | L2b | `navy_gordura` (coeficientes) |
| Texto das limitações, clamps, erros do plano e das janelas, e avisos das telas (L2b); do diário (L4: quantidade do item, macro ausente, total incompleto, entrada da refeição, água/fome, semana, balanço, meta retroativa) e os selos/dicas do diário em `NOTICE_LABELS` com o MESMO texto da limitação (`target_frozen`, `target_backfilled`, `intake_incomplete_days`, `training_unknown_future_day`) | `nutrition/labels.py` (`LIMITATION_LABELS`, `PREFIXED_LIMITATION_LABELS`, `PLAN_FIELD_LABELS`, `PLAN_STATE_LABELS`, `NOTICE_LABELS`, `HUNGER_LABELS`, `UNIT_LABELS`, `limitation_label`, `limitation_items`) | a API devolve `{code, label}` (no diário: `balance.limitations`, `target_notices`, limitações do item e do total); avisos pelo bloco `notices` do JSON (`nutritionNotice`); a tela só exibe | texto de limitação ou aviso escrito na rota ou na tela; número no texto escrito à mão (o dono interpola `FFM_FRESHNESS_MAX_DAYS`, `CUT_MAX_DEFICIT_SHARE`, `SCALE_BMR_DEVIATION_WARN_SHARE`, `TARGET_FREEZE_LAG_DAYS`, `TARGET_RECOMPUTE_MAX_DAYS`; no L4 `MEAL_MAX_ITEMS`, `ITEM_AMOUNT_MAX`, `HUNGER_SCALE`, `WATER_ML_RANGE`, `MAX_DAILY_CARRYOVER_SHARE`, `MAX_WEEKLY_BANK_SHARE`, `DEFAULT_DENSITY_G_PER_ML`) | L2b, L4 | `rotulo_estrategia`, `diario_nutricao` |
| Uso e cota de IA (L5a, §54): features (`meal_parse` do L5b, `label_photo` do L6; o L5b/L6 usa o mesmo código como rótulo `feature` do `llm.usage`), status `pending` → `succeeded`/`failed`, cota diária por feature (30 e 10, plano §8.3, defaults de desenho) contando TODA tentativa (qualquer status) no dia do usuário, job type de cada feature (`nutrition-meal-parse`, `nutrition-label-ocr`; o prazo do sweep é o do worker perdido do M73 para ele: timeout da Lambda + folga), fechamento (o relato do worker vence a presunção `worker_lost` do sweep; nenhum outro estado terminal é reaberto; sweep no worker, a cada fechamento) e custo por modelo (tabela em centavos de dólar por 1 milhão de tokens, com fonte e data; modelo fora da tabela ou token ausente → NULL; snapshot datado usa o preço do modelo-base; sem tokens de cache o custo é um teto); desde o §74, também os modelos Claude, a faixa de prompt longo do Haiku (`MODEL_LONG_PROMPT_PRICES`), a escrita no cache de 5 min da Anthropic (1,25× a entrada: `ANTHROPIC_CACHE_WRITE_5M_MULTIPLIER`, `ModelPrice.cache_write_cents`, argumento `cache_write_tokens_in`; virada Haiku de 2026-10-31) e o custo da periodização (`periodization.logs.estimate_cost_usd` só converte a escala); texto do 429 com a cota interpolada | `nutrition/ai_usage.py` (`AiFeature`, `AiUsageStatus`, `TERMINAL_STATUSES`, `OPEN_STATUS`, `SWEEP_PRESUMED_STATUS`, `worker_may_close`, `MEAL_PARSE_DAILY_QUOTA`, `LABEL_PHOTO_DAILY_QUOTA`, `DAILY_QUOTA`, `quota_status`, `QuotaStatus`, `MEAL_PARSE_JOB_TYPE`, `LABEL_PHOTO_JOB_TYPE`, `FEATURE_JOB_TYPE`, `feature_for_job_type`, `MODEL_PRICES_USD_CENTS_PER_MTOKEN`, `MODEL_LONG_PROMPT_PRICES`, `HAIKU_LONG_PROMPT_THRESHOLD_TOKENS`, `ANTHROPIC_CACHE_WRITE_5M_MULTIPLIER`, `PRICE_SOURCE_URL`, `PRICES_CHECKED_ON`, `ANTHROPIC_PRICE_SOURCE_URL`, `ANTHROPIC_PRICES_CHECKED_ON`, `price_for_model`, `estimate_cost_usd`, `ERR_AI_QUOTA_EXCEEDED`) + `labels.py` (`AI_FEATURE_LABELS`, `ai_quota_exceeded_label`) | `nutrition_ai_quota.py` (o SQL do `ai_usage_log` nos dois backends: `open_usage`/`reserve_usage` com a trava do usuário e o 429, `link_job`/`attach_job`, `finish_usage`, `usage_today`/`count_usage_today`, `sweep_stale_pending`; o "hoje" por `time_windows.user_day_start_db_for`; prazo e `worker_lost` de `async_jobs`); rotas e workers do L5b/L6 (o nome do job type importado do dono em `async_jobs.create_job(MEAL_PARSE_JOB_TYPE)` e `ai_usage.MEAL_PARSE_JOB_TYPE` como chave de `JOB_TYPE_LAMBDA`); FE pela API | cota, preço, casas do custo ou job type escritos na rota, no worker, no SQL ou na tela; contar só sucesso; prazo do sweep reescrito (é o `worker_lost_deadline_seconds` do M73); custo calculado fora de `estimate_cost_usd` (no escopo de nutrição, também o `periodization.logs.estimate_cost_usd` e os `OPENAI_PRICE_*_PER_MTOKEN`, de política oposta, §54 decisão 7); cota sobrescrita por variável de ambiente ou Terraform; regra de fechamento reescrita fora de `worker_may_close`; gravar no `ai_usage_log` fora do serviço; reserva dentro de transação longa ou em volta de chamada externa | L5a | `cota_ia_nutricao`, `cota_ia_copia_nutricao`, `relogio_sem_fuso` |
| Leitura da refeição por IA (L5b, §55): vocabulário que a IA usa (as unidades do diário menos a porção do catálogo), a estimativa por 100 g (os campos da RDC 429 do catálogo, sem sódio, validada pelas regras de entrada do catálogo), o teto do texto, quantos candidatos por item, a decisão do casamento (aceite automático do `food_text` pré-seleciona; candidato sem aceite → a pessoa escolhe; sem candidato → a estimativa da IA, natureza ESTIMATED, candidato a cadastro; sem nada → buscar à mão), a melhor de duas buscas (com e sem o preparo), a quantidade proposta (os gramas totais da IA; a medida em volume não vira massa pela densidade 1 g/ml), a origem do item do diário (catálogo OU estimativa) e o item estimado no diário (a conta de `meal_math`, natureza ESTIMATED, snapshot `ai_estimate`); a refeição lida grava `ai_text` | `nutrition/meal_parse.py` (`AI_UNITS`, `ESTIMATE_FIELDS`, `ESTIMATE_REQUIRED_FIELDS`, `ESTIMATE_KEY`, `ESTIMATE_SOURCE`, `ESTIMATE_BASIS`, `UNSTATED_QUANTITY_UNIT`, `TEXT_MAX_CHARS`, `CANDIDATES_PER_ITEM`, `ITEM_LABEL_MAX_CHARS`, `CONFIDENCE_RANGE`, `CATALOG_CANDIDATE_STATUSES`, `MatchStatus`, `validate_text`, `normalize_ai_items`, `normalize_confidence`, `validate_estimate`, `AiItem.match_queries`, `proposed_quantity`, `best_ranking`, `classify_item`, `build_result`, `estimate_snapshot`, `snapshot_is_estimate`, `estimated_item_nutrition`, `meal_source`) + `diary.py` (`item_source_errors`) + `labels.py` (`MEAL_PARSE_MATCH_STATUS_LABELS`, textos das limitações) | `nutrition_meal_parse_ai.py` (prompt e schema interpolados do dono; modelo `OPENAI_NUTRITION_PARSE_MODEL`; teto `openai_client.LLM_OUTPUT_OBSERVED_MAX_TOKENS[meal_parse]`), `nutrition_meal_parse_worker.py` (a busca da tela por `food_text` sobre `nutrition_access.diary_foods_visibility`; fecha o `ai_usage_log`), `nutrition_meal_parse.py` (202 + cota do L5a), `nutrition_meals.py` (item estimado e `ai_text`), FE (`MealParseModal`, `nutrition-meal-parse.ts`, `nutrition-meal-form.ts`, bloco `meal_parse` do JSON gerado) | limiar de nota, candidatos, campos ou chave da estimativa, unidade da IA, teto do texto ou decisão do casamento escritos no prompt, no worker, na rota ou na tela; converter a medida da IA pela densidade fora de `proposed_quantity`; nutriente do item estimado calculado fora de `estimated_item_nutrition`; número escrito à mão no prompt (`api/privado/tests/unit/test_nutrition_meal_parse_prompt.py`, ex-`test_nutrition_prompts_snapshot.py`, M103 P1 §73.1); tela que decide pela nota | L5b | `leitura_refeicao_ia`, `catalogo_alimentos`, `nota_matching`, `soma_nutrientes` |
| Foto do rótulo (L6, §56): upload só em JPEG (o tipo pela assinatura dos bytes) até `LABEL_PHOTO_MAX_BYTES` (1,5 MiB), num JSON com a foto em base64 sem padding (`decode_upload`; 413/415/400 ANTES da cota), uma imagem por leitura, chave `nutrition-labels/{user_id}/{uuid}.jpg` (`label_photo_key`; o worker só lê a chave do próprio usuário, `is_label_photo_key_of`), retenção de 30 dias (espelho da regra `nutrition-labels-expire-30d`, paridade), redução no navegador (borda 1280 px, qualidade 85%, sempre reencodada por canvas, nunca o arquivo cru), `detail` da imagem; revisão da leitura da IA: base g/ml (sem base lida, a padrão com aviso), kcal de kJ por `meal_math.kj_to_kcal`, "sem quantidade significativa" (a IA só lista o campo) vira zero do motor com aviso, por 100 derivado da porção (`meal_math.per_reference_from_serving`, aviso por campo), regras de entrada do `catalog` (erros bloqueiam, avisos não, Atwater), porção × por 100 pela tolerância de `meal_math.label_consistency`, GTIN por `external_sources`, foto ilegível sem pré-preenchimento; fonte do cadastro `label_photo` aceita no POST (`catalog.USER_ENTRY_SOURCES`) | `nutrition/label_photo.py` (`LABEL_PHOTO_MAX_BYTES`, `LABEL_PHOTO_MAX_MIB`, `BYTES_PER_MIB`, `LABEL_PHOTO_MAX_BASE64_CHARS`, `BASE64_BLOCK_BYTES`, `BASE64_BLOCK_CHARS`, `LABEL_PHOTO_CONTENT_TYPE`, `LABEL_PHOTO_ACCEPTED_CONTENT_TYPES`, `LABEL_PHOTO_EXTENSION`, `LABEL_PHOTO_S3_PREFIX`, `LABEL_PHOTO_RETENTION_DAYS`, `LABEL_PHOTO_IMAGES_PER_READING`, `RESIZE_MAX_EDGE_PX`, `RESIZE_JPEG_QUALITY_PERCENT`, `RESIZE_JPEG_QUALITY`, `LABEL_IMAGE_DETAIL`, `PREFILL_DECIMALS`, `AI_NOTES_MAX`, `AI_NOTE_MAX_CHARS`, `ENERGY_KJ_FIELD`, `LABEL_NUTRIENT_FIELDS`, `LabelLegibility`, `decode_upload`, `upload_error`, `label_photo_key`, `is_label_photo_key_of`, `clean_ai_notes`, `review_reading` → `LabelReview`) + `meal_math.py` (`per_reference_from_serving`) + `catalog.py` (`USER_ENTRY_SOURCES`, `user_entry_source`) + `labels.py` (`LABEL_LEGIBILITY_LABELS`, `LABEL_REGULATION_REFERENCE`, textos dos códigos `label_*`) | `nutrition_label_photo.py` (rota: 413/415/429/503, 202 com o prazo do polling de `async_jobs.worker_lost_deadline_seconds`), `nutrition_label_storage.py` (S3), `nutrition_label_ai.py` (prompt e schema montados do dono; Pydantic; `job_result`), `nutrition_foods.py` (`source` do POST), `lambda_plan_generator.py` (despacho); FE: `image-resize.ts` (parâmetros de quem chama), `nutrition-api.readLabelPhoto` e `LabelPhotoUpload` (bloco `nutrition.label_photo` do JSON gerado) | limite, tipo, prefixo S3, retenção, borda ou qualidade escritos fora do dono (rota, worker, tela, helper); a tela enviar o arquivo cru quando a redução falha; conta de nutriente (Atwater, porção, kJ) no worker, no prompt ou na tela; número escrito à mão no prompt (o teste renderiza com marcadores e exige zero dígito) ou a IA conferindo/calculando (tolerância, zero do "não significativo"); multipart com os bytes crus da foto; gravar a leitura no catálogo sem o usuário salvar | L6 | `foto_rotulo_nutricao`, `foto_rotulo_numeros`, `kj_kcal`, `soma_nutrientes` |

## 4. Inventário (referência)

Consolidado em 2026-10-06 (3 varreduras + conferência manual + medição em produção, só
leitura). Linhas de `origin/develop` nessa data — podem deslocar; a guarda e o `grep`
são a referência viva.

**A — transversais**
- e1RM: SQL `analytics.py` (`_SQL_RIR`, `best_e1rm`), `exercise_performance.py` (Epley/RIR inline),
  `exposure_service.best_e1rm` sem teto, `volume_service` dose sem `supports_e1rm`/sem filtrar
  técnica; textos "sem RPE → sem e1RM" em `metrics_registry.py`, `exercise-progress.ts`,
  `report_ai.py` (contradizem o meio-termo). ✅ Lote 3a (§13).
- RIR/defaults: `volume_service.py` (reps 8 / RPE 7,0 soltos; cópia em SQL). ✅ Lote 3a (§13).
- ✅ Série de trabalho (Lote 3b): `sql_not_preparatory` sem apelidos; `recovery_read_model.py` com `CASE`
  próprio; FE `workout-mode-types.ts`, `exercise-set-edit.ts`, `workout-mode-technique.ts`,
  `workout-mode-previous-session.ts` (`backoff` como working); `progression_engine.is_linked_application`.
  ✅ Lote 3a (§13): `services.compute_exercise_stats` incluía aplicações em `best_weight_kg/avg_rpe`.
- ✅ `trained_at` repetido em `volume_service`, `recovery_read_model`, `muscle_summary_service`,
  `adherence_service`, `technique_volume` (e `report/daily_breakdown`, achado do Lote 0);
  `created_at` é UTC → ~3h de erro quando falta `started_at` (18 de 73 atividades/90d) — dono único
  `time_windows.trained_at_sql` com o `created_at` convertido para o fuso do usuário (Lote 2b).
- ✅ Pesos por papel: ~~`muscle_recovery_service.participation` 0,30 (morto)~~ (apagado no Lote 2c); ~~`volume_service._resolve_contributions`
  0,50~~; `001_schema.sql` 0,50 (seed, fica: igual ao dono); ~~`exercise_mapping_catalog.py` 0,50~~; ~~FE `MuscleContributionsSection.tsx`
  1/0,5/0,25~~; ~~`muscle_groups_mapped.py` + `muscle-group-trend.ts` `SECONDARY_FACTOR=0.5` ignorando
  `stimulus_weight`~~; ~~`get_lowest_volume_groups` sem peso~~ (apagado no Lote 2c); ~~resolver devolve `[]` × `VolumeService` cai em "Outros"~~ (Lote 3c, §14).
- ✅ Recorde (Lote 3b): `domain/equipment_record.py` (desempate por reps, 1ª exposição é recorde, mistura
  equipamentos) e `intelligence/progression_service.get_recent_progression` (`MAX(best_weight_kg)` por nome).

**B — recuperação/prontidão**
- B1 snapshot sem decisão compõe teto com `READY` fixo (`domain/prescription.py`, `services.py`).
- B2 quatro regras de dose reduzida (`progression_engine.py` `ceil(S×0,75)`, `_RECOMMENDATIONS`
  "1–2 séries", `workout_plan_templates_engine.py` 2 séries somadas, `activation_window.py`
  orçamento ×0,75; Modo B só avisa em `mode_b.py`; prompts "1-2 séries / RPE máx. 8-9").
- B3 retrato × projeção (`recovery_projection.py` expira sinais em ≥72h; `days`/`limit`
  diferentes em `workout_plan_ai.py`, `freeze_mode_b.py`, `template_volume_planner.py`,
  `recovery_signals_service.py`).
- ✅ B4 alerta de fadiga 0,92/0,85 (`dashboard_alert_service.py`) × 0,95/0,90 do modelo — apagado (Lote 2c).
- B5 fator de técnica: catálogo `060` (forced 1,20) × `_SET_TYPE_FACTORS` (1,05).
- ✅ B6 `estimated_recovery_hours` próprio em `/analytics/training-volume` (`volume_service.py`) — removido (Lote 2c).
- B7 `get_consecutive_day_recovery_checks` soma a dose do dia.
- B8 `PAIN_DAILY_SQL` com todos os papéis (`recovery_signals_service.py`).
- ✅ B9 `_recovery_phase` (`analytics.py`), `home_readiness_pct` (`dashboard_aggregator_service.py`),
  `RecoveryPhaseTimeline.tsx` (morto) — apagados (Lote 2c).
- ✅ B10 fuso: `CURDATE()`/`date.today()`/`datetime.now()` em `volume_service.py`, `mode_a.py`,
  `activation_window.py`, `deterministic_prescription.py` (lista completa na allowlist da guarda) —
  resolvido no Lote 2b (§12).
- B11 prompt entrega TSB "para ajustar" (`workout_plan_ai.py`); RAG `HABILIDADES_TREINO.md` "72h obrigatórias".
- B12 `muscle_recovery_coefficients` sem escritor (0 linhas) — leitura removida no Lote 2c; drop da tabela pendente.

**C — progressão/prescrição/segurança**
- C1 três motores ativos: v2, legado +5% (`progression_service.suggest_load_and_reps`, vivo em
  `GET /exercises/suggested-progression`, `/analytics/dashboard-progression-suggestion`,
  `dashboard-muscle-drilldown`, fallback do congelamento em `services.py`) e IA por série
  (`exercise_set_evaluation_ai.py`).
- C2 `suggested-progression` nunca sobe carga (faixa derivada da própria sessão; conta aquecimento).
- C3 +5% sem passo × +5/+2,5 kg no passo; `_parse_reps_range("12")` → `(None, None)`.
- C4 dois parsers e três cortes de `rpe_target` (`prescription.parse_rpe`, `training_history.PrescribedSpec`,
  `effective_intensity.py`, `periodization/validation.py`, `cap_rpe_target_for_reduced_dose`).
- C5 `overlay ?? prescrição` em `PlanExecutionScreen.tsx` (o certo está em `intensity-status.ts`).
- C6 `safety_event`/`failure_allowed` em dois lugares (`effective_intensity`, `analytics._failure_allowed_for_rows`).
- C7 FE calcula aquecimento/ajuste/drop (`workout-mode-load-suggestions.ts`), ignora `application_loads_kg`;
  stepper ignora `load_increment_kg` (`weight-units.ts`).
- C8 `load_convention` ignorada por v2, legado e IA (3/542 exercícios com convenção).
- C9 "Ajuste de hoje" mistura v2 e legado (`domain/exercise_briefing.py`).
- C10 cópias: `DEFAULT_MAX_SAFE_RPE` ×3, `_READINESS_DEFAULTS` = `_RECOMMENDATIONS`,
  `REDUCED_DOSE_MULTIPLIER`, janela de 21 sessões ×3.
- C11 textos/prompts: `rag/markdown/PROGRESSAO.md` ("+5%", bloco que contradiz D4),
  `exercise_set_evaluation_ai.py`, `exercise_summary_ai.py`, `workout_plan_templates_engine.py`
  ("2,5%-5%"), `MUSCLE_FIT.md` (×0,95), `progression_service.py` ("pronto para progressão").
  **Lote 1c:** resolvidos todos, exceto `progression_service.py` (nota "pronto para progressão"
  com a ação manter) e o "2,5%-5%" de `workout_plan_ai.py`, que ficam com o Lote 1a (arquivos do
  backend de prescrição). `PROGRESSAO.md` passou a dizer que a decisão vem pronta do motor e
  perdeu os números à mão de regra com dono (incremento, faixas de volume, peso do secundário,
  limiar de fadiga, dose de deload).
  **Achado do Lote 1c (produção, 2026-10-06):** `progression_decisions` só tem decisões de
  GRUPO (236/236 com `track_id` NULL, `suggested_load_kg` sempre NULL). A única decisão por par
  exercício × equipamento é o snapshot congelado da ficha (`workout_plan_exercises.prescription_json`:
  `decision`, `suggested_load_kg`, `reps_target`, `effective_rpe_target`); é ele que os prompts
  consomem. A carga da PRÓXIMA sessão só existe quando a próxima ficha é congelada — o serviço
  `suggest_for_pair` do Lote 1 é quem deve expô-la antes disso.
- C12 `exercise_performance._same_load/_top_load_reps/_paired_top_reps` copiam `comparability`. ✅ Lote 3a.
- ✅ C13 rótulos triplicados (`metrics_registry`, `exercise_briefing`, `dashboard-status.ts`);
  `exercise-progress.ts` reimplementa `keys_match` (Lote 3c, §14).
- C14 briefing com janela de 91 dias e filtro por equipamento antes de classificar.

**D — recomendação/insights**
- D1 ranking de equipamento: `domain/equipment_ranking.py` × `exercise_substitution.py` ×
  `equipment-recommend.ts`; prompts dizem "ordenados do menos usado".
- D2 `insight_rules.py` com `+7/+14/+28` dias, `horizon=3` e `72.0` fora de `THRESHOLDS`.
- ✅ D3 `dashboard_alert_service.py` sem consumidor (fadiga, `LOW_VOLUME`, `ASYMMETRY`, `SIBLING_IDS`) — apagado (Lote 2c).
- ✅ D4 `ExerciseSimilarityService`/`recommend_alternatives` sem chamador; `recommend_focus_groups`
  só em `/analytics/dashboard-home` — apagados com a rota (Lote 2c).

**E — fichas/Modelo/volume**
- ✅ E1 normalização semanal em 3 versões (`methodology_volume.py`; `activation_window.resolve_window`
  `max(7, ciclo)`; `muscle_summary_service.py` ativação × 7/ciclo; `analytics.py` `/dashboard/ciclo`) — Lote 4a.
- ✅ E2 `/dashboard/ciclo`: realizado ponderado semanal + projetado bruto; projeção 0 no Modo B — Lote 4a.
- ✅ E3 técnica avançada some fora do Modelo (`MuscleActivationPreview.tsx`, `ExercisesTable.tsx`,
  breakdown em `volume_service.py`, resumo em `mode_b.py`) — Lote 4b (§18).
- ✅ E4 `template_slot_targets` do Modo B em séries brutas × ativação ponderada;
  `MuscleActivationTable.tsx` com limiares 0,9/0,5/6/9 — Lote 4b (§18).
- ✅ E5 slot recolhido: `slot-group-sets.ts` (sem técnica) ao lado do "9/12" do backend — Lote 4b (§18).
- ✅ E6 `muscle-activation-7d.ts` reavalia a metodologia no FE — Lote 4b (§18).
- E7 tempo de treino com dono no FE (`template-time-estimate.ts`) e porte em `periodization/time_estimate.py`;
  descanso default 0/60/90 s; paridade só de `totalSeconds`.
- E8 defaults `"8-12"`, `"8-9"`, `sets_count` 3/2/1, descanso 30/90, `round((min+max)/2)` espalhados.
- E9 tetos de exercícios/sessão 6–8–12 × 9 × 12; séries/exercício 4 × 6 × 9.
- E10 números à mão em prompts de periodização (`periodization/context.py`, `workout_plan_templates_engine.py`,
  `template_ai_selector_v2.py`) e RAG `HIPERTROFIA.md` "9-10 séries semanais".
- ✅ E11 `activation_projection.py` sem a tolerância de ±5% — Lote 4a.
- ✅ E12 fatia→bloco em `markdown_report_service.py`; `size_class` ainda aceito em `exercise_mapping_catalog.py` — Lote 4a
  (a coluna fica no banco até a migração de contract).
- ✅ E13 `intensity-density.ts` era o dono, só no FE (sem divergência) — desde 2026-10-13 o dono é
  `domain/intensity_density.py` e o TS é espelho gerado com fixture de paridade (§37).
- E14 código morto/legado: ✅ parte 1 (Lote 5a) `workout_plan_ai.generate_plan_draft/build_prompt/FULLBODY_*`,
  `abc_workout_ai.py`, `abc_workout_template.py`; parte 2 (pendente, por flag): `mode_*_legacy.py`,
  `template_ai_selector_v2.py`, reparo de `template_volume_validator.py`, `workout_plan_templates_engine.py` v1.

### Achados novos da varredura do Lote 0 (não estavam no inventário)

- `intelligence/report/daily_breakdown.py` também reescreve `trained_at` (2×) — entra em A/B10.
- `CURDATE()` em SQL de motor é bem mais difundido que os 4 pontos listados em B10:
  `analytics.py` (15), `volume_service.py` (8), `training_load_service.py` (7, semana
  calculada por `WEEKDAY(CURDATE())`), `recovery_read_model.py` (2),
  `intelligence/progression_service.py` (2) e `periodization/logs.py` (limite diário de
  geração por `CURDATE()`). No Aurora, `CURDATE()` é a data UTC: entre 21h e 0h (Brasília) a
  janela "dos últimos N dias" já é a de amanhã.
- `muscle_groups_mapped.py` recebe `secondary_factor` com default 0,5 na própria query
  (`Query(0.5)`), além do `SECONDARY_FACTOR` do FE — a tela Tendência tem duas cópias.
- `exercise_mapping_catalog._SECONDARY_CONTRIBUTION_WEIGHT = 0.50` (já citado em A como
  `:262`) é uma constante nomeada, mas fora do dono.

## 5. A guarda (ratchet)

`api/tests/unit/test_guarda_fontes_da_verdade.py` roda no job **backend unit** do CI
(`.github/workflows/ci-develop.yml`, `pytest tests/unit`), sem banco.

- Varre `api/app/**/*.py` e `frontend/src/**/*.ts(x)`, **sem** testes, `node_modules`,
  `.next` e `__pycache__`, e **sem** comentários e docstrings (prosa explicando a regra não
  é cópia). Strings contam: SQL e prompt são onde as cópias moram.
- Varre também `api/privado/rag/markdown/**/*.md` (submódulo privado desde o M103 P0, §73) (inteiro: Markdown injetado em prompt conta como prompt,
  princípio 4), mas só para os padrões que declaram a linguagem `md` (hoje `percentual_de_carga`,
  `dose_reduzida_texto`, `numeros_prompt_volume`, `exposicoes_minimas`, `fator_carga_trabalho_texto`
  e `faixa_reps_prosa`; desde o L1 de nutrição, também `tmb_formula`, `fator_atividade`,
  `kcal_por_grama`, `met_compendium`, `navy_gordura`, `densidade_energetica`, `faixa_proteina`,
  `ffmi_normalizado` e `pausa_dieta`). Um padrão pode ter `regex_md` própria, usada só nos `.md`
  (texto corrido tem números que no código seriam regra): hoje só `kcal_por_grama`, que no RAG pega
  apenas "4/7/9 kcal/g" / "kcal por grama".
- Varre também os templates de prompt `api/privado/prompts/**/*.md` (M103 P1, §73.1; o README não) com o
  MESMO rigor dos literais `.py` de onde a prosa saiu: todo padrão que declara `py`, a `regex` de código (não a
  `regex_md`), o escopo do módulo consumidor (`CONSUMIDOR_DO_PROMPT`: pasta do template → módulo de `api/app/` que a lê, ex.: `cardio_agent_ai.py`,
  `insights_prompt.py`, `goal_prompt_context.py`, `periodization/context.py`)
  e nenhuma isenção de dono. Pasta nova de prompt sem consumidor registrado reprova
  (`test_todo_template_de_prompt_tem_consumidor`); sem o submódulo, `test_guarda_varre_os_prompts_do_submodulo_privado`
  reprova.
- `escopo` aceita pasta (`/` no fim), arquivo ou glob com `*` (`fnmatch`): os padrões de nutrição com
  número genérico (1,55; × 4; 3 + 1) valem só em `ESCOPO_NUTRICAO`, que já inclui os arquivos que os
  lotes L2+ vão criar (`api/app/nutrition_*.py`, `api/app/external_food_sources.py`,
  `frontend/src/lib/nutrition-*`…). A seção de nutrição do relatório Markdown
  (`markdown_report_service`/`markdown_renderer`, L9) e a fase derivada no `context_service` (L8)
  entram no `ESCOPO_NUTRICAO` quando esses lotes criarem o código (hoje os dois arquivos existem sem
  nutrição; incluí-los inteiros agora traria ruído de outros domínios).
- **Falso positivo conhecido em nutrição** de `limiar_fadiga` (`0.85`) e `dose_reduzida` (`0.75`):
  o aceite automático do matching (`food_text`, 0,85) e o multiplicador do dia baixo do ciclo de
  carbo (`carb_cycling`, 0,75) coincidem na grafia com esses limiares. No dono de nutrição eles são
  escritos como `1.0 - MATCH_AUTO_ACCEPT_MAX_DISTANCE` e `1.0 - CARB_CYCLE_SWING_SHARE`. O escopo dos
  dois padrões NÃO é estreitado (isso afrouxaria a guarda de recuperação); a constante de nutrição
  se adapta.
- Migrações SQL (`api/db/migrations`) ficam fora: são seed/default de coluna versionados e
  só mudam por migração nova; quando divergem, entram aqui como divergência
  (ex.: `001_schema.sql` SECONDARY 0,50 em A).
- `ALLOWLIST` = arquivo → nº de ocorrências fora do dono, congelado em 2026-10-06. Falha se
  um arquivo **novo** ganhar ocorrência, se a contagem **subir**, ou se **cair** sem a
  allowlist ser reduzida junto ("reduza a allowlist para N"). O diff da allowlist é a prova
  de cada lote.

| Chave | Regra detectada | Dono | IDs | Lote que encolhe |
|---|---|---|---|---|
| `epley` | `/ 30` e `/ 30.0` | `domain/e1rm.py` | A, C12 | 3a (allowlist vazia) |
| `rir` | `10 - rpe`, `10.0 - ` | `domain/e1rm.py` | A, C12 | 3a (allowlist vazia) |
| `dose_sem_rpe` | `<reps/rpe> if … is not None else 8/7.0`, `COALESCE(x.rpe, 7)`, `rpe ?? 7` | `domain/e1rm.py` (`dose_inputs`) | A | 3a (nasceu vazia) |
| `dose_reduzida` | literal `0.75` | `muscle_recovery_service.py` | B2, C10 | 2 (2a: `progression_engine` saiu) |
| `dose_reduzida_texto` | "1-2 séries diretas" / "1-2 séries, RPE" / "RPE máx. 8-9" (py, ts e RAG) | `muscle_recovery_service.py` (`reduced_dose_prompt_rule`) | B2 | 2a; 5a zerou o código morto (allowlist vazia) |
| `sinais_expiram` | `performance_signals_reset` / `PERFORMANCE_SIGNALS_RESET*` (proibição pura) | `muscle_recovery_service.py` (`SIGNALS_POLICY`) | B3 | 2a (zerado) |
| `defaults_prescricao` | `"8-12"` / `"8-9"` entre aspas | `domain/prescription_defaults.py` | E8 | 4c (26 → 8); 5a (8 → 0, allowlist vazia) |
| `descanso_default` | `min/max_rest_seconds = 30/90` em assinatura, `round((… rest + … rest) / 2)`, `DEFAULT_REST_SECONDS = 60`, `minRest… ?? 30`, `useState<number>(30/90)` | `domain/prescription_defaults.py` | E7, E8 | 4c (nasce com 12, todas em código morto); 5a (12 → 0, allowlist vazia) |
| `tetos_sessao` | `SESSION_EXERCISES_*`, `MAX_EXERCISES_PER_(SLOT/SESSION/DAY/GROUP)`, `*MAX_SETS_PER_EXERCISE*`, `MAX_PRIORITIZED_PER_SLOT`, `SLOT_TIME_LIMIT_*`, `COMPLEMENT_*` `= <número>`; `prioritized_count > <número>` | `periodization/limits.py` | E9 | 4c (nasce vazia) |
| `premissas_tempo` | `EXEC_SECONDS_PER_REP`, `DEFAULT_ESTIMATED_REPS`, `REST_WHEN_UNDEFINED_SECONDS` `= <número>` | `periodization/time_estimate.py` | E7 | 4c (nasce vazia) |
| `numeros_prompt_volume` | "N a M séries semanais/por semana", "entre 90 e 110", "pede N-M exercicios", "{ex_target_min}-<número>" (py, ts e RAG; proibição pura) | `periodization/context.py` (interpola a tabela) | E10 | 4c (nasce vazia) |
| `limiar_fadiga` | `0.92` / `0.85` (só Python) | `muscle_recovery_service.py` | B4, D3 | 2c (allowlist vazia) |
| `meta_fallback` | literal `27`/`27.0` (só Python) | `volume_service.py` | §4 Metas | — (hoje zero) |
| `peso_secundario` | `SECONDARY_FACTOR`; `secondary…` + `0.5`/`0.3`/`0.25` na mesma linha | `muscle_contribution_resolver.py` (`DEFAULT_WRITE_WEIGHTS`) | A | 3c (allowlist vazia) |
| `rotulo_progressao` | `"Progredindo"`, `"Regredindo"`, `"Estável"`, `"Suspeita de platô"`, `"Não comparável"` como literal entre aspas (com ou sem acento; py e ts) | `domain/progression_status_labels.py` | C13 | 3c (sobra 1 permanente) |
| `trained_at` | `COALESCE(x.started_at, x.ended_at` | `time_windows.py` | A, B10 | 2b (allowlist vazia) |
| `normalizacao_semanal` | `* 7 /`, `7.0 /`, `max(7,` / `max(WEEKLY_WINDOW_DAYS,` | `methodology_volume.py` | E1, E2 | 4a (sobra 1 permanente: `body_service`) |
| `relogio_sem_fuso` | `datetime.now()`, `date.today()`, `CURDATE()` (só módulos de motor, lista em `MODULOS_DE_MOTOR`) | `time_windows.py` | B10 | 2b (allowlist vazia) |
| `motor_legado_carga` | `suggest_load_and_reps` (só Python) | `domain/prescription_service.py` | C1, C2 | 1a (allowlist vazia) |
| `carga_percentual` | carga × `(1 + pct/0,0x)` ou × `1,0x` (+5% solto), `_adjust(0.05)` e, desde o R1, `carga`/`load`/`weight`/`peso` × `(1 ± x / 100)` (py e ts) | `progression_engine.py` (incremento em kg no passo) + `domain/next_set_load.py` (o percentual de uma regra cadastrada vira kg só em `_percent_load`) | C3, R1 | 1a (allowlist vazia); R1 (2026-10-20: dono e forma novos, segue vazia) |
| `rpe_texto_paralelo` | `cap_rpe_target_for_reduced_dose`, `_clamp_rpe_text`, `clamp_rpe_target_text`, `_cap_token` (só Python) | `effective_intensity.py` | C4 | 1a (allowlist vazia) |
| `percentual_de_carga` | `+5%`, ` -5%` (sinal colado) e faixas `2,5% a 5%` / `2,5%-5%` em código, prompt **e** `api/privado/rag/markdown` | `progression_engine.py` (`load_increment`: passo do equipamento, em kg) | C1, C3, C11 | 1 (1c zerou `PROGRESSAO.md` e `workout_plan_templates_engine.py`; 1a zerou `workout_plan_ai.py`; 5a zerou `abc_workout_ai.py` — allowlist vazia) |
| `fatores_carga_sessao` | `Math.pow(0.8`, `0.8 **`, `0.5 + 0.2 *`, `<carga> * 0.2/0.4/0.8` (Python e TS) | `domain/session_loads.py` | C7 | 1b (allowlist vazia) |
| `set_type_paralelo` | apelido `'dropset'` entre aspas, `CASE … set_type`, `set_type`/`setType` `===`/`!==` `'working'`/`'warmup'`/`'feeler'`/`'backoff'`, par `'warmup', 'feeler'` (Python e TS) | `set_types.py` + `frontend/src/lib/set-types.ts` | A | 3b (nasce vazia; 11 ocorrências em 6 arquivos removidas) |
| `recorde_paralelo` | `MAX(best_weight_kg)` e `get_recent_progression` (proibição pura) | `intelligence/dashboard/session_records.py` | A | 3b (nasce vazia) |
| `payload_ativacao_sem_tecnica` | objeto de uma linha com `mapping_id` e `sets_count` que fecha sem `advanced_technique` (py e ts) | `technique_volume.activation_payload` + `frontend/src/lib/activation-payload.ts` | E3 | 4b (sobra 1 permanente: o preview do Modelo já manda `planned_item_sets`) |
| `limiar_ativacao_fe` | `ratio >= 0.9`/`0.5` e `…equivalent < 6`/`9` (só ts) | `volume_service.assess_activation_against_target` | E4 | 4b (nasce vazia) |
| `metodologia_no_fe` | `classifyAgainstRange`, `frequencyBucketForDays`, `sessionRangeFor`, `weeklyRangeFor`, `makeBlockResolver` (proibição pura, só ts) | `intelligence/methodology_realized.py` | E6 | 4b (nasce vazia) |
| `fadiga_previa` | constante `PRIOR_FATIGUE_*`/`FADIGA_PREVIA_*` com número; `prior_fatigue`/`priorFatigue`/`prior_sets` (e campos, ex. `.level`) comparados (`<`, `>=`...) ou divididos por número (py e ts) | `domain/prior_fatigue.py` | F1 | 2026-10-07 (nasce vazia) |
| `trilha_sem_contexto` | `tracks.get((a, b))` / `tracks[(a, b)]` com tupla de duas posições (só py): trilha da progressão buscada só pelo par, sem o contexto de fadiga | `domain/progression_engine.py` (`track_key`/`resolve_track`) | F1 PR 2 | 2026-10-07 (nasce vazia; proibição pura) |
| `exposicoes_minimas` | "N ou mais exposições" / "mínimo de N exposições" (py, ts e RAG; proibição pura) | `metrics_registry.THRESHOLDS["plateau_min_exposures"]` (prompt interpola `report_ai.PLATEAU_MIN_EXPOSURES`) | IA-06 | 2026-10-08 (nasce vazia) |
| `fator_carga_trabalho_texto` | "N% da carga de trabalho" / "N% a M% da carga de trabalho" (py, ts e RAG; "100% da carga de trabalho" não casa) | `domain/session_loads.py` (`WARMUP_LOAD_FACTORS`, `FEELER_*`) | IA-06, C7 | 2026-10-08 (nasce vazia: `HABILIDADES_TREINO.md` HAB-042/043 e 6 trechos de `COSTAS.md` reescritos) |
| `faixa_reps_prosa` | "N a M repetições" / "N–M reps" em prosa, sem "antes" (RIR), só no RAG geral (`HABILIDADES_TREINO.md`, `HIPERTROFIA.md`, `PROGRESSAO.md`) | `domain/prescription_defaults.py` (`DEFAULT_REPS_TARGET`; alvo do Modelo) | IA-06, E8 | 2026-10-08 (nasce vazia: HAB-007, HAB-053 e panturrilha de `HIPERTROFIA.md` reescritos) |
| `numeros_prompt_cardio` | "N minutos (em) Z2", "N-M% VO2", "PAI … NN", "Nx [N minutos", "mínimo de N ocorrências" (py e ts; proibição pura) | `domain/cardio_policy.py` | IA-06 | 2026-10-08 (nasce vazia) |
| `cardio_prescricao_paralela` | `cardio_duration_minutes` comparado a número ou multiplicado por número; aquecimento/recuperação/HIIT comparados a número ou × número; `CARDIO_(DURATION\|WARMUP\|COOLDOWN\|SESSION)_*`/`HIIT_(INTERVAL\|ROUNDS)_* = <número>`; `SECONDS_PER_CARDIO_MINUTE` (py e ts) | `domain/cardio_prescription.py` | M98 C0 | 2026-10-26 (nasce vazia: as cópias saíram no mesmo PR) |
| `zonas_fc_paralelas` | "N% da FCmáx", "N-M% (da) FC", "N a M% da frequência cardíaca", `max_hr * 0.x` (py, ts e RAG; o dono guarda os percentuais em tupla nomeada) | `domain/hr_zones.py` | M98 C0 | 2026-10-26 (allowlist: `CARDIO.md` 15; zerou na C3a, §69.4) |
| `cardio_paralelo` | `_is_cardio_activity`, `_CARDIO_PATTERNS`, `LIKE '%cardio%'` (só py, `api/app/`; proibição pura: o dono usa `CARDIO_PATTERNS`/`is_cardio`) | `intervals_activity_kind.py` | M98 C2 | 2026-10-26 (allowlist: `intervals_integration.py` 5, `analytics.py` 2); **zerada na C4** (2026-10-27) |
| `aderencia_cardio_paralela` | `ADHERENT_*_PCT`/`PARTIAL_*_PCT = N` e `time_completion_pct`/`target_zone_completion_pct`/`high_zone_completion_pct` comparados a número fora do dono (py e ts) | `domain/cardio_adherence.py` | M98 C2 | 2026-10-26 (nasce vazia) |
| `envelope_cardio_paralelo` | `WEEKLY_HIIT_*`/`WEEKLY_CARDIO_SESSIONS_*`/`HIIT_MIN_TSB`/`TSB_HIGH_RISK_BELOW`/`RECOMMENDATION_HORIZON_DAYS`/`HRV_DROP_BAD_PCT`/`RESTING_HR_RISE_BAD_BPM`/`RECOVERY_SIGNAL_DAYS`/`HIGH_INTENSITY_MIN_SECONDS`/`CONTINUOUS_SESSION_MAX_MINUTES = N`, `form_tsb`/`tsb` comparado a número negativo e `LEG_MUSCLE_GROUPS = (` fora do dono (py e ts) | `domain/cardio_envelope.py` | M98 C3a | 2026-10-26 (nasce vazia) |
| `numeros_rag_cardio` | "N minutos", "N min", "N vezes", "Nx por semana", "N sessões", "NN%" no `api/privado/rag/markdown/CARDIO.md` (só md; proibição pura: os números entram por placeholder `{{cardio.*}}`) | `app/rag_placeholders.py` (mapa placeholder → constante do dono) | M98 C3a | 2026-10-26 (nasce vazia) |
| `pesos_esforco_paralelos` | `EPOC_ZONE_WEIGHTS`/`PAI_ZONE_WEIGHTS = (`, `ROLLING_WINDOW_DAYS = N` e `epoc…`/`pai… * 0.x` (ou `+= 0.x`) fora do dono (py e ts) | `domain/cardio_load.py` | M99 | 2026-10-27 (nasce vazia) |
| `pior_lado` | `min`/`max`/`Math.min`/`Math.max`/`LEAST`/`GREATEST` sobre `reps_left`/`reps_right`/`rpe_left`/`rpe_right` (reps/RPE oficiais da série unilateral calculados à mão) fora do dono (py e ts, repo inteiro) | `domain/set_sides.py` | M102 | 2026-10-27 (nasce vazia) |
| `e1rm_confiavel_paralelo` | `<= E1RM_MAX_RELIABLE_REPS` / `<= THRESHOLDS["e1rm_max_reliable_reps"]` fora do dono (predicado de confiabilidade reescrito) | `domain/e1rm.py` (`is_reliable_reps`) | COD-04 | 2026-10-08 (allowlist: 1 filtro SQL de `analytics.py`) |
| `tonelagem_convertida` | "tonelada(s)" em código, prompt ou RAG (py, ts e md; proibição pura): a tonelagem sai só em kg, pronta, de `format_tonnage` | `load_conventions.py` | — (resumo de treino, eval 2026-10-10) | 2026-10-10 (nasce vazia) |
| `queda_reps_entre_series` | razão `1 − última/primeira` de reps (`1.0 - (float(last…reps`, `1 - lastSet.reps`) e `rep…drop… = / > 0.3` (py e ts) | `intelligence/volume_service.py` (`_rep_drop_from_sets`) | RPE10-P2 | RPE 10 Lote 1 (2026-10-11, nasce vazia) |
| `vocabulario_ia_serie` | categorias antigas da IA por série entre aspas (`'PROGREDIR'`, `'APRIMORAR TECNICA'`, `'REDUZIR'`, `'REGREDIR'`; py e ts; proibição pura) | `domain/set_evaluation_categories.py` | RPE10-P9 | RPE 10 Lote 6 (2026-10-11, nasce vazia) |
| `limiar_dor_muscular` | `muscle_pain`/`musclePain` comparado com literal 1-9 (`> 6`, `>= 4`; py e ts; proibição pura — `> 0` não casa) | `intelligence/muscle_recovery_service.py` (`MUSCLE_PAIN_BLOCK_ABOVE`, `muscle_pain_blocks_direct_work`) | SET-EVAL-V5 | set-eval-v5 (2026-10-12, nasce vazia) |
| `rotulo_status_serie` | rótulo do status de esforço / reps contra a faixa entre aspas ("Alvo atingido", "Abaixo do alvo seguro", "Esforço atingido", "Esforço abaixo do alvo", "Reps … da faixa"; py e ts) | `domain/intensity_status_labels.py` | SET-EVAL-V5 | set-eval-v5 (2026-10-12, nasce vazia) |
| `carga_proxima_serie` | limiar da regra da próxima série escrito à mão: `reps > 15`/`> 16`, `reps >= 12`/`>= 16`, `reps < 6`/`<= 5`, "mais de 15/16 repetições", "antes de 5/6 repetições" e constante `NEXT_SET_* = <número>` fora do dono (py, ts e RAG; proibição pura — `reps <= 12` do e1RM não casa); desde o R1 também o conjunto padrão redefinido (`DEFAULT_RULESET =`, `RuleSet(reps_floor=<número>`) | `domain/next_set_load.py` | SET-EVAL-V6, R1 | set-eval-v6 (2026-10-12, nasce vazia); R1 (2026-10-20, segue vazia) |
| `regra_carga_cadastrada` | condição de uma regra de carga avaliada fora do dono: `reps_condition`/`rpe_condition` comparado (`<`, `>=`, `==`…) com número, `facts`, `reps` ou `rpe`; `rule.reps.op`/`regra.rpe.value` comparado (`===`, `!=`); `magnitude.value / 100` (py e ts em `api/app/` e `frontend/src/`; fora: `frontend/src/app/(app)/regras-carga/` e `frontend/src/lib/next-set-load-rules*.ts`, a tela do R4 que só monta Selects; proibição pura) | `domain/next_set_load.py` (`evaluate_ruleset`, `_compare`, `_percent_load`) | R1 | R1 (2026-10-20, nasce vazia) |
| `intensidade_densidade` | `RPE_INTENSITY_POINTS`, `*_DENSITY_*`, `SETS_VOLUME_*`, `BALANCED_BAND`, `FAILURE_LAST_SET_RPE` atribuídos a número (ou a `{ min: 6 …` / `[{ minRpe: 10 …`), `minRpe: <número>` e `linear(x, 6\|0\|180\|2, 15\|5\|45)` (py e ts) | `domain/intensity_density.py` | E13 | 2026-10-13 (o TS era o dono; espelho lê o JSON, nasce vazia) |
| `pesos_tecnica` | `forced_reps`/`negative_reps`/`partial_reps`/`cheat_reps` `: <número>` e `DEFAULT_MAX_EQUIVALENT`/`FULL_EFFORT_RPE`/`DEFAULT_REFERENCE_RPE = <número>` (py e ts) | `domain/technique_accounting.py` | TEC-ESPELHO | 2026-10-13 (a cópia do TS saiu, nasce vazia) |
| `pontos_esforco` | par `(6.5, 0.15)`/`[10, 1.05]` da tabela de esforço e o clamp `, 6.5, 10)`/`Math.max(6.5` (py e ts) | `intelligence/muscle_recovery_service.py` (`_EFFORT_POINTS`, `effort_points`) | TEC-ESPELHO | 2026-10-13 (a cópia do TS saiu, nasce vazia) |
| `modo_contagem_volume` | literal `'primary_only'`/`'primary_secondary'` entre aspas e `SECONDARY_SESSION_MIN_SETS = <número>` fora do dono (py e ts; o consumidor Python usa `VolumeCountingMode` e a constante importada; o FE lê de `motor-constants.generated.json` via `volume-counting-mode.ts`) | `intelligence/methodology_volume.py` | MODO-CONTAGEM | modo de contagem, Lote 1 (2026-10-16, nasce vazia); Lote 3 (§40) estende a ts/tsx e a `'primary_only'` |
| `faixas_sem_modo` | `FROM`/`JOIN muscle_volume_class_mode_ranges` ou `muscle_volume_class_weekly_targets` sem `counting_mode =` na mesma linha (só py; proibição pura — o loader dono e o CRUD filtram pelo modo; as tabelas da 057 são o cadastro de `primary_only` e ficam fora) | `intelligence/methodology_volume_service.py` (`load_volume_classes`) | MODO-CONTAGEM | modo de contagem, Lote 2 (2026-10-16, nasce vazia) |
| `plato_por_trilha` | `PLATEAU_MIN_SESSIONS`, `PLATEAU_MIN_SPAN_DAYS`, `GROUP_PHASE_ADVANCE_TRACK_SHARE` e `applied / total >` fora do dono (py e ts) | `domain/progression_engine.py` | RPE10-P6 | RPE 10 Lote 4 (2026-10-11, nasce vazia) |
| `carga_topo_repetida` | `TOP_LOAD_MIN_SHARE_OF_WORKING_SETS` e `ceil(len(...) / 2)` / `Math.ceil(x.length / 2)` (ou `* 0.5`) fora do dono (py e ts): a regra "a carga de topo se repete em metade das séries" recalculada | `domain/progression_engine.py` (`session_working_load`, `top_load_min_sets`) | RPE10-L7-D1 | RPE 10 Lote 7 (2026-10-11, nasce vazia) |
| `rotulo_site_medida` | rótulo pt-BR de local de medida entre aspas ou crase (`'Cintura'`, `"Quadril"`, `` `Pescoço` ``, `'Braço esquerdo'`…) ou como texto JSX (`>Cintura<`); `Peitoral` só como par `chest: "Peitoral"` (py e ts) | `domain/body_measurement_sites.py` (`SITE_LABELS`) | NUT-L0 | Nutrição L0 (2026-10-15, nasce vazia: 3 cópias em Python e 1 no TS removidas; crase e JSX desde 2026-10-16) |
| `origem_kcal` | origem da kcal decidida fora do dono: `calories_source` comparado com literal (`=== 'manual'`, `== "intervals"`, py e ts), gravado como literal (`calories_source = 'intervals'` em SQL), escolhido por condicional (`CALORIES_SOURCE_MANUAL if …`), ou a kcal da atividade comparada com a do link (`a.calories_kcal = ail.calories_kcal`, `==`/`===`/`<=>`) | `domain/calories_source.py` (`resolve_calories_source`, `source_on_manual_write`) | NUT-L0 | Nutrição L0, revisão do #556 (2026-10-16, nasce vazia: a rota do `PATCH /meta` decidia `manual` e passou a consumir o dono) |
| `tmb_formula` | `370 + 21,6`, `500 + 22`, `6,25 ×`, `88,362`, `447,593` e `MIFFLIN_*`/`KATCH_MCARDLE_*`/`CUNNINGHAM_*`/`HARRIS_BENEDICT_*` = número (py, ts e RAG; repo inteiro) | `domain/nutrition/bmr.py` | NUTRICAO-L1 | L1 nutrição (2026-10-16, nasce vazia) |
| `fator_atividade` | `1,375`/`1,55`/`1,725`, tabela por nível (`moderate: 1.4`, `ActivityLevel.LIGHT: 1.3`) e `ACTIVITY_FACTORS`/`NON_EXERCISE_ACTIVITY_FACTORS = {` (py, ts e RAG; só `ESCOPO_NUTRICAO`) | `domain/nutrition/anthropometrics.py` | NUTRICAO-L1 | L1 nutrição (2026-10-16, nasce vazia) |
| `kcal_por_grama` | `* 4`/`* 9`/`/ 4`/`/ 9` e `4 * x`/`9 × (` (não `* 4.184`, `'1 / 4'`, `14 * x`), tabela por macro (`fat: 9`, `protein: 4`; não `protein_g: 4.5`), "4 kcal/g"/"9 kcal por grama" e `KCAL_PER_G_* = <número>` (py e ts; no RAG só a `regex_md` de kcal/g; só `ESCOPO_NUTRICAO`; `meal_math` consome `kcal_from_macros` e não é dono) | `domain/nutrition/macros.py` | NUTRICAO-L1 | L1 nutrição (2026-10-16, nasce vazia) |
| `met_compendium` | `/ 200`, `0,0175` (= 3,5/200) e `MET_STRENGTH_*`/`MET_KCAL_* = <número>` (py, ts e RAG; `ESCOPO_NUTRICAO` + `workout_sessions.py`) | `domain/nutrition/energy_expenditure.py` | NUTRICAO-L1 | L1 nutrição (2026-10-16, nasce vazia) |
| `navy_gordura` | `86,010`/`70,041`/`163,205`/`97,684`, forma métrica `1,0324`/`0,19077`/`0,15456`/`1,29579`/`0,35004` e `NAVY_* =` (py, ts e RAG; repo inteiro) | `domain/nutrition/body_composition.py` | NUTRICAO-L1 | L1 nutrição (2026-10-16, nasce vazia) |
| `densidade_energetica` | `7.700`/`7700` e `9.400`/`9400` soltos (não `R$ 9.400,00`) e `FAT_/LEAN_TISSUE_KCAL_PER_KG = <número>` (py, ts e RAG; repo inteiro) | `domain/nutrition/energy_density.py` | NUTRICAO-L1 | L1 nutrição (2026-10-16, nasce vazia) |
| `faixa_proteina` | `N g/kg` / `N,N g por kg` (0–3, inteiro ou decimal; pega também o carbo mínimo, mesmo dono), `*PROTEIN*_G_PER_KG* = <número>` e as chaves das faixas do formulário com número (`protein_g_per_kg`, `fat_kcal_share`, `surplus_share`, `weekly_rate_share`, `carb_min_g_per_kg`, `max_deficit_share`, `min_intake_bmr_multiple`, com sufixo, `:` ou `=`) (py, ts e RAG; repo inteiro, mais estrito que o plano: HYP-031/032 conferidos sem g/kg) | `domain/nutrition/strategy.py` + `macros.py` | NUTRICAO-L1 | L1 nutrição (2026-10-16, nasce vazia) |
| `ffmi_normalizado` | `6,1 × (1,8` / `6,1·(1,8` e `FFMI_* = <número>` (py, ts e RAG; repo inteiro) | `domain/nutrition/body_composition.py` | NUTRICAO-L1 | L1 nutrição (2026-10-16, nasce vazia) |
| `pausa_dieta` | "2 on/2 off", `3+1`/`3:1`, "N semanas … M semanas" e `MATADOR_*`/`ICECAP_* = <número>` (py, ts e RAG; só `ESCOPO_NUTRICAO`) | `domain/nutrition/phases.py` | NUTRICAO-L1 | L1 nutrição (2026-10-16, nasce vazia) |
| `rotulo_estrategia` | `"Bulking"`, `"Cutting"`, `"Manutenção"`, `"Dia alto/baixo em carbo"`, `"Pausa de dieta"` entre aspas ou como texto JSX (`>Cutting<`) (py e ts; o plano pedia só ts) | `domain/nutrition/labels.py` (FE via `nutrition-constants.ts`) | NUTRICAO-L1 | L1 nutrição (2026-10-16; allowlist com 2 arquivos da INTENÇÃO da metodologia de volume, ver §45) |
| `kcal_treino_prioridade` | `calories_kcal or/??/\|\| <estimativa>` e o ternário `calories_kcal if … else <estimativa>` (fallback para placeholder — `'—'`, `0`, `None`, `null` — não casa) e `TRAINING_KCAL_*PRIORITY =` (py e ts; repo inteiro) | `domain/nutrition/energy_expenditure.py` (`resolve_training_kcal`) | NUTRICAO-L1 | L1 nutrição (2026-10-16, nasce vazia) |
| `vocabulario_sexo` | opção com código de sexo (`value: 'M'`/`'F'`/`'male'`/`'female'`), `sex…` comparado ou atribuído a literal (`sex === 'F'`, `"sex": "M"`, `row.get("sex") == "male"`, `WHERE sex = 'M'`, `sex <> 'F'`, `sex IN ('M', 'F')`, `sex?: 'male'`; inclui `'other'` e `m`/`f` minúsculos), rótulo `masculino/a`/`feminino/a` entre aspas ou crase em qualquer caixa, e texto JSX (`>Feminino<`) (py e ts; repo inteiro; `value: 'other'` sozinho não casa) | `domain/nutrition/anthropometrics.py` + `labels.py` (FE via `nutrition-constants.ts`) | NUT-L2A | L2a nutrição (2026-10-17, nasce vazia: 4 + 4 ocorrências de `PerfilClient.tsx` e `users-admin-types.ts` removidas) |
| `nivel_atividade_e_altura` | `activity_level…` comparado ou atribuído a código literal (`=== 'active'`, `= 'light'`, `IN ('light', …)`), opção `value: 'sedentary'`/`'very_active'`, rótulo `sedentário/a`/`muito ativo/a` entre aspas ou como texto JSX, `height_cm…` comparado com número (`< 100`, `>= 250`) e `HEIGHT_CM_RANGE` redefinido (py e ts; repo inteiro; "Leve", "Ativo", `value: 'light'`/`'active'` sozinhos são genéricos e não casam) | `domain/nutrition/anthropometrics.py` + `labels.py` (FE via `nutrition-constants.ts`) | NUT-L2A | L2a nutrição (2026-10-17, revisão do guardião; nasce vazia) |
| `catalogo_alimentos` | constante do catálogo redefinida com número fora do dono: `MATCH_MIN_SCORE`, `MATCH_AUTO_ACCEPT_*`, `QUERY_COVERAGE_WEIGHT`, `TOKEN_JACCARD_WEIGHT`, `SEQUENCE_RATIO_WEIGHT`, `SEARCH_CANDIDATE_POOL`, `SEARCH_RESULT_LIMIT_*`, `FULLTEXT_MIN_TOKEN_CHARS`, `PORTION_AMOUNT_MAX`, `ML_BASIS_MAX_DENSITY_G_PER_ML` `= <número>`; `SYNONYMS`/`STOPWORDS`/`NOISE_TOKENS` redefinidos (`= {`, `= (`, `= frozenset`, `= new Set`) (py e ts; repo inteiro; comparação ou uso do nome do dono não casa) | `domain/nutrition/food_text.py` + `catalog.py` | NUT-L3A | L3a nutrição (2026-10-17, nasce vazia) |
| `nota_matching` | nota do matching de alimento comparada com número (`score >= 0.6`, `nota > .85`, `m["score"] < 0.6`) — só no escopo de nutrição (proibição pura: candidato e pré-seleção vêm de `rank_candidates`/`order_for_search`, `Match.auto_accept`) | `domain/nutrition/food_text.py` | NUT-L3A | L3a nutrição (2026-10-17, revisão do guardião; nasce vazia) |
| `fontes_externas_alimentos` | mapeamento ou regra das fontes externas fora do dono, só no escopo de nutrição: id de nutriente do USDA (1003, 1004, 1005, 1008, 1062, 1079, 1093, 1257, 1258, 2047, 2048; nunca seguido de casa decimal; o 2000 fica fora por ser genérico), chave de nutriente do OFF em literal (`"energy-kcal"`, `"saturated-fat"`, `"trans-fat"`, `"added-sugars"`, `"*_100g"`), literal de tipo/preparo (`"Foundation"`, `"SR Legacy"`, `"as_sold"`), estrutura crua dos payloads (`foodNutrients`, `nutrientId`, `aggregated_set`, `nutriments`, `value_computed`, `fdcId`, `dataType`, `totalHits`), constante do dono redefinida (`OFF_LOOKUPS_PER_USER_DAY`, `OFF_LOOKUP_COUNTED_SOURCE`, `USDA_SEARCH_PAGE_SIZE`, `GTIN_LENGTHS`, `GS1_*`, `USDA_*`/`OFF_*` de ids, chaves e candidatos) e EAN validado à mão (`\d{13}`, `\d{12,14}`, `% 10`). Sem padrão (genéricas demais; cobertas pela estrutura crua): as chaves `energy`, `fat`, `sodium`, `proteins`, `sugars`, `fiber`, `carbohydrates` do OFF | `domain/nutrition/external_sources.py` | NUT-L3B | L3b nutrição (2026-10-17; ampliado na revisão do guardião; nasce vazia) |
| `kj_kcal` | conversão kJ ↔ kcal escrita à mão (`4.184`/`4,184`) ou `KJ_PER_KCAL` redefinido com número, repo inteiro (py e ts) | `domain/nutrition/meal_math.py` (`KJ_PER_KCAL`, `kj_to_kcal`, `kcal_to_kj`) | NUT-L3B | L3b nutrição (2026-10-17, revisão do guardião; nasce vazia) |
| `rotulo_sem_macro_grupo` | o rótulo `Sem macro-grupo` (com essa caixa) em string ou texto JSX fora do dono, repo inteiro (py e ts); "sem macro-grupo" em prosa minúscula não casa | `intelligence/methodology_volume.py` (`VOLUME_CLASS_UNCLASSIFIED_LABEL`; FE via `volume-counting-mode.ts` → `UNCLASSIFIED_VOLUME_CLASS_LABEL`) | VOLUME-AGRUPAMENTO | /volume-grupamentos por macro-grupo (2026-10-17, §50; nasce vazia: 4 cópias do FE removidas) |
| `bom_para_paralelo` | instante "Bom para" refeito fora do dono: `timedelta(hours=/minutes=/seconds=…remaining_hours/estimated_recovery_hours/estimated_hours…)` (py) e `remaining_hours`/`estimated_recovery_hours` × `3600`/`3_600_000`/`3600e3`/`36e5`/`3.6e6`/`60 * 60`, nas duas ordens dos operandos (py e ts; repo inteiro; por linha: `timedelta(` quebrado em várias linhas escapa; proibição pura — o dono busca o 1º minuto READY pela projeção) | `intelligence/recovery_projection.py` (`ready_forecast`) | BOM-PARA | 2026-10-17 (nasce vazia, §51) |
| `diario_nutricao` | constante do diário, da semana ou do congelamento redefinida com número fora do dono: `MEAL_MAX_ITEMS`, `ITEM_AMOUNT_MAX`, `HUNGER_SCALE`, `WATER_ML_RANGE`, `DEFAULT_DENSITY_G_PER_ML`, `PER_100G_REFERENCE`, `ATWATER_TOLERANCE_SHARE`, `LABEL_SERVING_TOLERANCE_SHARE`, `MAX_DAILY_CARRYOVER_SHARE`, `MAX_WEEKLY_BANK_SHARE`, `ADHERENCE_*`, `MIN_LOGGED_DAYS_FOR_WEEK`, `TARGET_FREEZE_LAG_DAYS` `= <número>`/`= (`/`= [`; tabela de medidas caseiras reescrita (`colher_sopa: 15`, `xicara: 240`, `GENERIC_PORTIONS = {`) (py e ts; repo inteiro; uso do nome do dono não casa) | `domain/nutrition/meal_math.py` + `diary.py` + `weekly_budget.py` + `daily_target.py` | NUT-L4 | L4 nutrição (2026-10-17, nasce vazia) |
| `soma_nutrientes` | soma de nutrientes de itens, refeições ou dias fora do dono (`sum(`/`SUM(`/`.reduce(`/`+=` sobre `energy_kcal`, `protein_g`, `carbohydrates_g`, `total_fat_g`, `fiber_g`, `sodium_mg` ou as metas da 079 `kcal_target`/`carbs_g`/`fat_g`; também a acumulação `total = total + item.energy_kcal`) e escala por 100 g/ml escrita à mão (`/ 100`, `* 0.01`) — só no escopo de nutrição. Proibição pura no dono (soma por `NUTRIENT_FIELDS`, escala por `PER_100G_REFERENCE`): uma soma em SQL ou no FE trataria o NULL do item como 0 e perderia o total incompleto (decisão 1 do L4) | `domain/nutrition/meal_math.py` + `weekly_budget.py` + `macros.py` (`Macros.plus`) | NUT-L4 | L4 nutrição (2026-10-17, nasce vazia) |
| `cota_ia_nutricao` | uso de IA da nutrição fora do dono: constante de `ai_usage` redefinida com número ou tabela (`MEAL_PARSE_DAILY_QUOTA`, `LABEL_PHOTO_DAILY_QUOTA`, `DAILY_QUOTA`, `FEATURE_JOB_TYPE`, `MODEL_PRICES_USD_CENTS_PER_MTOKEN`, `TOKENS_PER_PRICE_UNIT`, `CENTS_PER_USD`, `COST_USD_DECIMAL_PLACES` `= <número>`/`= {`/`= (`/`= [`/`= MappingProxyType`/`= frozenset`), job type `nutrition-meal-parse`/`nutrition-label-ocr` em literal (a rota e o dispatch do L5b/L6 importam o nome do dono; a chave de `async_jobs.JOB_TYPE_LAMBDA` é `ai_usage.MEAL_PARSE_JOB_TYPE`), variável de cota ("cota"/"quota" como parte do nome, snake ou camelCase; `cotacao` não casa) comparada ou atribuída a número (`cota_usada >= 30`, `remainingQuota === 0`, `quota !== 30`, `quota: int = 10`, `"meal_parse_quota": 30`; `: 0` de inicialização não casa) e tabela de preço copiada (`"gpt-…": <número>`, `ModelPrice(`) (py e ts; repo inteiro; sem padrão para `label_photo`, que é também `FoodSource.LABEL_PHOTO`, nem para `meal_parse`, chave do teto do `openai_client`: o L5b/L6 cobre com teste de paridade) | `domain/nutrition/ai_usage.py` | NUT-L5A | L5a nutrição (2026-10-18, nasce vazia; ampliado na revisão do guardião) |
| `cota_ia_copia_nutricao` | limite diário ou preço de IA paralelo, só no `ESCOPO_NUTRICAO`: `*_DAILY_LIMIT`, `MAX_PER_DAY`, `*PARSE/OCR/QUOTA/COTA/CALLS/REQUESTS*_PER_DAY` e `LIMITE_DIARIO` (qualquer caixa) `= <número>` ("por dia" sozinho, como `kg_per_day`, é unidade da nutrição e não casa), cota ou preço lidos de variável de ambiente (`os.environ.get`/`os.getenv`/`process.env` com `QUOTA`/`COTA`/`PER_DAY`/`DAILY`/`PRICE` no nome), `OPENAI_PRICE_*` e `periodization.logs` (o custo da periodização, de política oposta, §54 decisão 7). Os precedentes que o L5b/L6 copiaria usam esses nomes (`DAILY_GENERATION_LIMIT`, `ENV_MAX_PER_DAY` dos cards); fora da nutrição eles são de outros donos. Proibição pura (py e ts) | `domain/nutrition/ai_usage.py` | NUT-L5A | L5a nutrição (2026-10-18, revisão do guardião; nasce vazia) |
| `leitura_refeicao_ia` | constante da leitura da refeição por IA redefinida fora do dono, também com prefixo (`MEAL_PARSE_TEXT_MAX_CHARS = 1000` no FE casa): `TEXT_MAX_CHARS`, `CANDIDATES_PER_ITEM`, `ITEM_LABEL_MAX_CHARS`, `CONFIDENCE_RANGE` `= <número>`/`= (`/`= [`; `AI_UNITS`, `ESTIMATE_FIELDS`, `ESTIMATE_REQUIRED_FIELDS`, `ESTIMATE_KEY`, `CATALOG_CANDIDATE_STATUSES`, `UNSTATED_QUANTITY_UNIT`, `ESTIMATE_BASIS` `= (`/`= [`/`= {`/string/f-string/`frozenset`/`new Set`) ou a chave da estimativa em literal (`"estimated_per_100g"`: o schema e o worker usam `ESTIMATE_KEY`) (py e ts; repo inteiro; os limiares do casamento são de `catalogo_alimentos` e `nota_matching`) | `domain/nutrition/meal_parse.py` | NUT-L5B | L5b nutrição (2026-10-18, nasce vazia) |
| `foto_rotulo_nutricao` | foto do rótulo fora do dono, repo inteiro (py e ts): constante de `label_photo` redefinida (`LABEL_PHOTO_MAX_BYTES`, `LABEL_PHOTO_MAX_MIB`, `LABEL_PHOTO_CONTENT_TYPE`, `LABEL_PHOTO_ACCEPTED_CONTENT_TYPES`, `LABEL_PHOTO_EXTENSION`, `LABEL_PHOTO_S3_PREFIX`, `LABEL_PHOTO_RETENTION_DAYS`, `LABEL_PHOTO_IMAGES_PER_READING`, `RESIZE_MAX_EDGE_PX`, `RESIZE_JPEG_QUALITY`, `RESIZE_JPEG_QUALITY_PERCENT`, `LABEL_PHOTO_MAX_BASE64_CHARS`, `LABEL_IMAGE_DETAIL` `= <número>`/`= "…"`/`= int(`/`= frozenset`...), o prefixo `nutrition-labels` em literal, com ou sem a barra (a chave sai de `label_photo_key`; o IAM e o lifecycle do `.tf` têm teste de paridade; o id `nutrition-labels-expire-30d` não casa) e 1,5 MiB à mão (`1572864`, `1.5 * 1024`, `1024 * 1024 * 1.5`, `1536 * 1024`) | `domain/nutrition/label_photo.py` | NUT-L6 | L6 nutrição (2026-10-18, nasce vazia; ampliado na revisão do guardião) |
| `foto_rotulo_numeros` | parâmetro da redução ou do upload escrito à mão, só no `ESCOPO_NUTRICAO` e em `frontend/src/lib/image-resize.ts`: `1280`, `quality`/`qualidade` com fração ou inteiro, sozinha ou com prefixo de imagem (`quality: 0.85`, `jpegQuality = 85`, `JPEG_QUALITY = 85`; `DataQuality`/`MIN_DATA_QUALITY` não casam), `toBlob(…, 0.85)` (também com arrow function), `0.85`/`.85` solto, `maxEdge: <número>`, o teto do corpo em base64 (`2097152`), `"image/jpeg"` em literal e o `detail` da imagem em literal (`detail: "high"`, `ImageInput(…, "high")`; `"high"` sem âncora não, por `DayKind.HIGH`) (o helper não tem default; a tela lê o JSON gerado; rota, armazenamento e worker usam o tipo e o `detail` do dono) | `domain/nutrition/label_photo.py` | NUT-L6 | L6 nutrição (2026-10-18, nasce vazia; ampliado na revisão do guardião) |
| `fallback_registro_serie` | piso de séries ou valor inicial do registro da série à mão: `sets_count ?? N`/`\|\| N`/`or N` (N ≥ 1; `?? 0` de soma não casa), `parseNumericTarget(…) ?? N` (até um nível de parênteses dentro) e `SET_LOGGER_FALLBACK_* = N` fora do dono (a tela e o núcleo leem `MIN_SETS_COUNT`/`SET_LOGGER_FALLBACK_*` do JSON gerado); desde o B3 também `max(N, …sets/target/remaining…)`, a ordem invertida `max(…sets/remaining…, N)` e `…remaining/target… ?? N`/`\|\| N` (de fora, conscientemente: nome sem sets/target/remaining, expressão entre parênteses e ternário; §57.2) | `domain/prescription_defaults.py` | E8, ZEPP-B2, ZEPP-B3 | B2 da Evolução ZEPP (2026-10-19, nasce vazia, §57.1) |
| `modo_treino_pela_ficha` | só no Modo Treino (`modo-treino/`, `lib/workout-mode-*`): disjunção de duas ou mais comparações `advanced_technique`/`warmup_reps`/`feeler_set_reps` `!= null` na mesma linha, ou o filtro legado `has_advanced_technique` (de fora, conscientemente: disjunção em várias linhas e forma truthy; §57.3) | `frontend/src/lib/workout-mode-advanced-stage.ts` | ZEPP-B4 | B4 da Evolução ZEPP (2026-10-19, nasce vazia, §57.3) |
| `fallback_registro_serie_cadeia` | só no Modo Treino (`modo-treino/`, `lib/workout-mode-*`): número de 2 dígitos no fim de uma cadeia de `??` de reps/carga (`lastSet?.reps ?? 10`, `suggested_load_kg ?? 20`) | `domain/prescription_defaults.py` | E8, ZEPP-B2 | B2 da Evolução ZEPP (2026-10-19, nasce vazia, revisão do guardião) |
| `medida_serie` | medida da série comparada ou atribuída a literal (`== 'duration'`, `=== 'max_reps'`, `es.measure = 'duration'`, `'max_reps' in …`, `measure == 'reps'`) fora do dono (py e ts, repo inteiro; acesso pelo Enum, funções do dono e chaves de JSON não casam) | `domain/set_measures.py` (FE via `set-measures.ts`, que lê o JSON gerado) | T1 | T1 medidas de série (2026-10-20, nasce vazia, §60) |

Entradas **permanentes** (falso positivo semântico, documentado no próprio teste):
`metrics_registry.py` em `dose_reduzida` (0,75 de participação e de delta de RPE),
`dashboard/body_service.py` em `normalizacao_semanal` (pesagens por semana, não volume) e
`VolumeGrupamentosClient.tsx` em `rotulo_progressao` ("Estável" da tendência de VOLUME do grupo).
Entradas de **outro domínio** (saem com o dono delas, não com a nutrição): `report/markdown_renderer.py`
(1) e `lib/methodology-volume.ts` (2) em `rotulo_estrategia` — "Manutenção"/"Cutting" da INTENÇÃO da
metodologia de volume (§3.5, linha "Rótulo da intenção", backlog M84), escritos à mão hoje. É o
inventário inicial do padrão: não cresce, e sai quando o M84 criar o dono do rótulo.
`rotulo_sem_macro_grupo` nasce vazia (§50): as 4 cópias do FE (fallbacks de `lib/counting-mode-comparison.ts`,
`lib/methodology-volume.ts` e `lib/volume-classes.ts` e o título do bloco da aba admin, `VolumeClassesPanel.tsx`)
passaram a ler o rótulo exportado por `methodology_volume.export_for_frontend()`, e o relatório importa do dono.

## 6. O que ficou fora da guarda (e por quê)

- **Faixas de repetições nos RAGs de grupo e nos Modelos-exemplo** (`PEITO.md`, `PANTURRILHA.md`,
  `TIPOS_TREINO/2. ABC/*.md`...): ~200 ocorrências que descrevem o protocolo de um exercício ou
  de um Modelo-exemplo, não o default do app. `faixa_reps_prosa` varre só o RAG geral (IA-06).
- **"Histórico de 14 dias" no prompt do cardio**: descreve o contexto padrão; a janela real é o
  `days` da chamada (7/14/30) e não é regra de cálculo do agente. Fica como está (IA-06).
- **`6.0` / `9.0` da meta de fallback**: ruído demais (RPE 9.0, `FULL_EFFORT_RPE`,
  `min_rpe=9.0`). A meta é detectada pelo teto `27`, que só existe ali.
- **0,85 no frontend**: é opacidade de CSS, não limiar de fadiga — `limiar_fadiga` varre só
  Python. (Até 2026-10-13 também era o ponto de esforço do RPE 9 copiado em
  `technique-accounting.ts`; desde §37 a tabela vem do JSON gerado e `pontos_esforco` a guarda.)
- **Parser de RPE, ranking**: não têm um literal estável que uma regex pegue sem ruído. Ficam
  no mapa e nos testes de unidade dos donos.
- **Nutrição sem padrão (L1, §45)**: multiplicadores do ciclo de carbo, limiares do TDEE adaptativo
  (14 dias, 80%), banco (10%/5%), aderência (5%/90%), matching (0,6/0,85) e porções caseiras não têm
  literal que uma regex distinga de outros números (0,8; 0,05; 15 ml…). Ficam nas constantes nomeadas
  dos donos e nos testes `test_nutrition_*.py`. **Keytel** (FC → kcal) não tem padrão porque saiu do
  motor (D-K, 2026-10-15); se voltar, volta com padrão.
- **Série de trabalho e recorde** ganharam padrão no Lote 3b (`set_type_paralelo`,
  `recorde_paralelo`), mas só para as formas estáveis. Fora da guarda, de propósito: códigos de
  TÉCNICA do catálogo (`"drop_set"`, `"forced_reps"` em `technique_accounting`, rótulos
  "Drop"/"Mini-bloco" por técnica no FE) — outro vocabulário, que coincide na grafia; estágios do
  Modo Treino (`kind === 'warmup'`); e a comparação de recorde em Python (a regra é uma
  comparação de números, sem nome proibível — coberta pelos testes de `session_records`).

## 7. Como adicionar uma regra nova

Toda regra de cálculo nova — **limiar, fator, fórmula, janela, default** — segue três passos
no mesmo PR:

1. **Módulo dono.** Use o dono existente da área (tabelas acima) ou crie um módulo Python
   puro, sem I/O, com a constante **nomeada**. Consumidores (rota, SQL, lambda, relatório,
   FE, prompt) importam do dono; o FE recebe o valor pronto da API ou tem espelho com
   teste de paridade contra fixture do backend; prompt/RAG **interpola** o valor.
2. **Registro no mapa.** Adicione a linha na tabela da seção 3 (regra → dono → proibido
   fora do dono) e, se substituir uma cópia, atualize a divergência/lote.
3. **Guarda.** Se a regra tem um literal ou expressão proibível fora do dono, adicione um
   `Padrao` em `test_guarda_fontes_da_verdade.py` com o dono, o ID e exemplos positivos e
   negativos em `EXEMPLOS`; rode o teste e registre a allowlist do estado atual
   (idealmente vazia). Ao remover cópias, **reduza** a allowlist no mesmo PR. Número genérico
   demais para o repo inteiro vai com `escopo` (pasta, arquivo ou glob — ex.: `ESCOPO_NUTRICAO`,
   que cobre os arquivos que os lotes seguintes ainda vão criar).
4. **Impacto medido (progressão).** Mudança de semântica no Motor de Progressão v2 mede o
   impacto com `api/scripts/replay_diff_progressao.py` (Lote 0 da investigação RPE 10,
   2026-10-11): com o DEV carregado por `scripts/prod_to_dev.py --user-id N`, rode
   `python scripts/replay_diff_progressao.py --user-id N --base-api <worktree da develop>/api`
   de dentro de `api/`. Ele roda `evaluate_history` em memória com o motor da `develop` e com
   o do branch (um subprocesso por versão, só leitura do banco) e devolve QUALIFY/RESET/NEUTRAL
   por motivo, decisões por tipo, fase por grupo, carga/séries por trilha, eventos de segurança
   e a lista de diferenças; o Markdown vai no PR como "Impacto em produção". Os cenários da
   investigação ficam fixados em `api/tests/unit/test_cenarios_rpe10_progressao.py`: o lote que
   muda a regra inverte a expectativa marcada `COMPORTAMENTO ATUAL (muda no Lote N/Pk)`.

## 8. Lote 1a (2026-10-06) — prescrição com um motor só (backend)

Resolvidos: **C1, C2, C3, C4, C6, C9, B1, C10** (parcial: `training-safety.ts` fica para o 1b,
`REDUCED_DOSE_MULTIPLIER` para o Lote 2) e o lado backend de **C5/C7**. Pendente: **C8**.

- **Um motor de sugestão por par**: `domain/prescription_service.py`
  (`suggest_for_pair`/`suggest_for_pairs`) chama `prescribe_pairs` com a segurança do par e o
  contrato de recuperação PROJETADO para agora (`freeze_mode_b.projected_readiness`). Consumidores:
  `GET /exercises/suggested-progression`, `/analytics/dashboard-progression-suggestion`,
  `dashboard-muscle-drilldown` (com o contrato já calculado na rota), o congelamento de linhas
  fora dos Modos A/B (`services._manual_rows_snapshots`) e os indicadores dos caminhos legados de
  geração (`workout_plan_ai.assign_progression_indicators`). `app/progression_service.py` foi
  apagado (motor +5%, `_parse_reps_range`, faixa `min..max+2` da própria sessão).
- **Sem equipamento informado**: vale o equipamento da última execução (se houver trilha nele);
  senão a trilha executada mais recentemente. Trilha sem carga: calibração pela execução
  (`session_working_load`, mínimo das séries de trabalho).
- **B1**: `build_prescription(readiness=…)` compõe com o contrato do grupo quando não há decisão;
  `apply_safety_overlay` nunca devolve teto/falha acima do congelado.
- **C4**: `"8-9-10"` vale **10** (último degrau) em todo lugar, cortado pelo teto do contrato;
  `clamp_rpe_text("8-9-10", 9) == "8-9"`.
- **C6**: `failure_allowed`/`intensity_status` dos analytics usam `intensity_for_row`, a mesma regra
  da gravação do `safety_event`.
- **C7 (backend)**: `domain/session_loads.py` é o dono de aquecimento 20%/40%, ajuste
  0,5 + 0,2·(n−1) e drop 0,8ⁱ, arredondados ao passo do equipamento (repetição de carga — rest-pause
  — não é arredondada). `progression_engine.technique_parameters` usa o mesmo dono.
- **Prompt**: `workout_plan_ai.build_prompt` interpola `COMPOUND/ISOLATION_BASE_INCREMENT_KG` no
  lugar de "2,5%-5%".

## 9. Lote 1b (2026-10-06) — Modo Treino lê cargas e teto do backend

Resolvidos no frontend: **C5, C7, C10** (parte TS). Allowlist: novo padrão `fatores_carga_sessao`
já nasce vazio (o FE tinha 3 ocorrências em `workout-mode-load-suggestions.ts`, removidas).

- **C5 (teto)**: o Modo Treino não compõe mais teto. Item da ficha: `prescription.effective_rpe_ceiling`
  (exibida, já com o overlay de hoje) e, sem snapshot, `safety_overlay.effective_rpe_ceiling`. Par
  trocado na sessão (override ou continuação sintética): `effective_rpe_ceiling` de
  `GET /exercises/suggested-progression` do par novo (a troca agora manda `equipment_id`). Treino livre:
  idem, e o RPE oferecido passa a ser cortado por ele. `resolveSafetyBannerCeiling` perdeu o `min`;
  ficou só a atribuição do texto (teto < `overlay.max_safe_rpe` = recuperação).
- **C7 (cargas)**: âncora = a carga prescrita (`load_anchor_kg`) → listas da API (`warmup_loads_kg`,
  `feeler_loads_kg`, `application_loads_kg`). Âncora dinâmica (série de trabalho real antes do drop,
  sugestão da IA, carga após trocar de equipamento) → espelho em `workout-mode-load-suggestions.ts`.
  Decisão: espelho com fatores exportados em vez de endpoint, porque o drop-set tem descanso de
  0–10 s e a academia tem sinal ruim; os fatores vêm de `frontend/src/lib/motor-constants.generated.json`
  e `session-loads.parity.json` (105 casos âncora × passo, incluindo meio-passo e passos em libras
  convertidas) prova a paridade no vitest. Ambos são gerados por
  `api/tests/unit/test_m76_lote1b_espelhos_fe.py --gravar`, que falha se divergirem do dono.
  A âncora da próxima série de trabalho é a última série `working` (antes podia ser um drop).
- **Stepper**: `weight-units.loadStepperStep(unit, load_increment_kg)` — modo simples anda no passo do
  equipamento (em lb, o equivalente convertido); sem passo, o padrão da unidade. O passo fino do modo
  avançado (0,25 kg) é ajuste de UX, não regra de carga.
- **C10 (TS)**: `training-safety.ts` lê `MIN/MAX/DEFAULT_MAX_SAFE_RPE` de `motor-constants.generated.json`.

## 10. Lote 2c (2026-10-07) — recuperação paralela sem consumidor apagada

Princípio 4 (código morto com regra paralela é apagado, não corrigido). Resolvidos: **B4, B6, B9,
D3, D4**; **B12** parcial (leitura removida, drop pendente).

- **B4/D3**: `intelligence/dashboard_alert_service.py` (fadiga 0,92/0,85, `LOW_VOLUME` 0,5/1,5,
  `ASYMMETRY` 0,30/0,55, `SIBLING_MUSCLE_GROUP_IDS`) e `GET /analytics/dashboard-alerts` apagados.
  O sucessor é `/analytics/dashboard/insights` (`insight_rules`); o par de grupos irmãos mora só em
  `metrics_registry.SIBLING_MUSCLE_GROUP_PAIRS`. O ranking `get_top_exercises_by_muscle_group`
  (M19) continua, porque o drill-down muscular o usa; `get_exercise_session_history` (agregação de
  e1RM paralela, sem chamador desde o M74) saiu.
- **B9/D4**: `_recovery_phase` (o campo `recovery_phase` sai de `/analytics/dashboard-recovery`),
  `GET /analytics/dashboard-home` com `build_home_payload`/`_compute_readiness`
  (`home_readiness_pct`), `ExerciseRecommendationService`/`recommend_focus_groups`,
  `ExerciseSimilarityService`/`recommend_alternatives`, `VolumeService.get_lowest_volume_groups`
  e, no FE, `RecoveryPhaseTimeline.tsx`, o domínio `recoveryPhase` de `dashboard-status.ts` e o tipo
  `RecoveryPhase`.
- **B6**: `/analytics/training-volume` não devolve mais `estimated_recovery_hours` por grupo (o tipo
  do FE perdeu o campo opcional; nenhuma tela o renderizava).
- **B12**: `MuscleRecoveryService._get_personal_coefficient` devolve `NEUTRAL_PERSONAL_COEFFICIENT`
  sem ir ao banco, e `recovery_read_model.REFERENCE_DATA_SQL` perdeu o `UNION` da tabela. Resultado
  idêntico (a tabela tinha 0 linhas em produção). **Pendente**: migração que dropa
  `muscle_recovery_coefficients` (destrutiva; decidir junto com o lote de migrações) — ou, se um
  dia houver calibração, reintroduzir a leitura só no dono.
- Também saíram: `MuscleRecoveryService.participation()` (0,30 morto),
  `muscle_summary_service.READINESS_HISTORY_DAYS` (sem uso) e `IntelligenceServiceBase._admin_user_id`
  (só servia à leitura do coeficiente).
- Rotas: `dashboard-alerts` e `dashboard-home` saíram de `infra/api_gateway.tf` e de
  `nginx/local-dev.conf` (paridade em `test_paridade_rotas_engines.py`).
- Prova de não-uso (logs da `musclefit-engines`, 2026-09-23 → 2026-10-06, todo o histórico retido):
  `dashboard-home` 0 chamadas; `dashboard-alerts` 1 chamada, **401** sem token (2026-09-25), nenhuma
  autenticada; `dashboard-recovery` 2 chamadas em 2026-09-23 (a rota fica, só sem `recovery_phase`);
  `training-volume` 6 chamadas (`/volume-grupamentos`, que não lê o campo removido).
- Guarda: `limiar_fadiga` zerou (allowlist vazia); `normalizacao_semanal` −1 (`dashboard_alert_service`);
  `relogio_sem_fuso` em `volume_service.py` 8 → 7 (`get_lowest_volume_groups` tinha `CURDATE()`).

## 11. Lote 2a (2026-10-07) — recuperação com uma regra só

Resolvidos: **B2, B3, B5, B7, B8, B11**. O 2c (código morto: B4, B6, B9, B12) está na §10; o 2b
(fuso, B10) é PR irmão.

- **B2** (decisão 1): `MuscleRecoveryService.reduced_sets(S, status)` = `ceil(S × 0,75)`, mínimo 1
  (READY devolve `S`; NO_DIRECT_WORK, 0). `reduction_policy()` e `reduced_dose_prompt_rule()` explicam a
  regra a partir das constantes. `_RECOMMENDATIONS["REDUCED_DOSE"]` deixou de dizer "1–2 séries diretas"
  (`direct_sets_min/max = None`, `minimum_sets`, `sets_rule`). Consumidores: `progression_engine.prescribe`
  (sem `REDUCED_DOSE_MULTIPLIER` próprio); **o Modo B reduz as séries do rascunho**
  (`mode_b.sets_for_recovery`; antes só avisava) e o congelamento parte das séries do Modelo para não
  reduzir duas vezes (`freeze_mode_b.base_sets_for_freeze`); planner do Modo A (`volume_multiplier_for`);
  `activation_window` (orçamento contínuo × o mesmo multiplicador; as séries de cada exercício vêm do
  motor); templates_engine v1 (sem o teto próprio de 2 séries somadas por grupo); Full Body
  (`FULLBODY_SETS_BASELINE_BY_DOSE` derivado); prompts interpolam `reduced_dose_prompt_rule()`. Tela:
  `recommendationLabel` escreve `sets_rule`.
- **B3** (decisão 2): `recovery_projection` não zera mais os sinais de desempenho ≥72h à frente
  (`SIGNALS_POLICY = "signals_persist_until_superseded"`; o campo `performance_signals_reset` saiu).
  Janela única: `RECOVERY_LOOKBACK_DAYS = 28` (a maior das antigas: com 14, a tela de 28 dias perdia
  sinais de sessões de 15–28 dias atrás, contra a decisão 2) e `RECOVERY_GROUP_LIMIT = 100` como defaults de
  `get_recovery_overview`; geração (`workout_plan_ai`), congelamento (`freeze_mode_b`), planner,
  `recovery_signals_service`, `muscle_summary_service` e contexto de cardio deixaram de passar
  `days`/`limit` próprios (`READINESS_WINDOW_DAYS` saiu). Ficam com `days` explícito só as rotas antigas
  que recebem `?days=` do cliente (`/analytics/muscle-recovery` e a drill-down), sem `limit` que corte grupo.
- **B5**: `technique_accounting.recovery_factor` lê só o catálogo para técnica (deriva o código do
  `set_type` quando a linha não tem) e técnica sem fator calibrado vale 1,00; `_SET_TYPE_FACTORS` ficou só
  com aquecimento, ajuste, backoff e trabalho.
- **B7**: `get_consecutive_day_recovery_checks` usa a dose e a participação da sessão mais recente de D1
  (mesma unidade do resumo), não a soma do dia.
- **B8**: `PAIN_DAILY_SQL` só conta dor de exercício com papel PRIMARY (ou sem contribuição cadastrada —
  fallback legado do modelo, em que o grupo do exercício é PRIMARY).
- **B11**: o bloco de Intervals.icu do prompt diz que o TSB já foi aplicado pelo modelo e não deve ajustar
  volume nem intensidade; RAG HAB-004 deixa de tratar 72h como exigência (é o teto, `RECOVERY_MAX_HOURS`).

Allowlist: `dose_reduzida` −1 (`progression_engine.py` saiu); `defaults_prescricao`
`workout_plan_templates_engine.py` 5 → 4; padrões novos `dose_reduzida_texto` (sobram 3 + 3 no código
morto `abc_workout_*`, Lote 5) e `sinais_expiram` (zerado).

## 12. Lote 2b (2026-10-07) — fuso do usuário como fonte única de dia e semana

Resolvido: **B10** e a linha **A (`trained_at`)**. As duas allowlists da guarda zeraram
(`trained_at` 16 → 0 em 6 arquivos; `relogio_sem_fuso` 39 → 0 em 10 arquivos).

**Dois relógios.** Hora de parede do usuário (naive): `activities.started_at`/`ended_at`,
`local_date`, `plan_date`. Relógio do banco (UTC; Aurora e MySQL do compose/CI com
`time_zone = SYSTEM` = UTC): tudo que nasce de `NOW()`/`CURRENT_TIMESTAMP` — `created_at`,
`effective_from`/`superseded_at` das versões de Modelo, `assigned_at`/`unassigned_at`,
`template_generation_logs.created_at`. Regra: toda conta de DIA, SEMANA ou JANELA usa o dia do
usuário; instantes do banco são convertidos antes de virar dia; o "hoje" entra no SQL como
**parâmetro** (nunca `CURDATE()`).

**Donos** (`intelligence/dashboard/time_windows.py`, o mecanismo que já existia — `user_now`,
`user_today`, `user_timezone` — estendido):
- `utc_offset_minutes(fuso)` — deslocamento do fuso (São Paulo = −180), lido do relógio que
  `congelar_relogio` congela;
- `trained_at_sql(alias, offset_minutes)` e `with_trained_at(sql, fuso)` (marcador `{trained_at}` em
  SQL constante de módulo) — `COALESCE(started_at, ended_at, DATE_ADD(created_at, INTERVAL −180
  MINUTE))`. Literal inteiro, sem `:` (o shim da Data API trataria como parâmetro) e sem depender das
  tabelas de fuso do MySQL (ausentes no compose/CI);
- `user_day_start_db` / `user_day_end_db` / `user_day_end_db_sql` — início e fim do dia do usuário
  no relógio do banco (fim do dia 04/10 de Brasília = 05/10 02:59:59 UTC);
- `db_instant_to_user_date` — dia do usuário de um instante do banco.

Nos serviços: `IntelligenceServiceBase._user_timezone_name/_user_now/_user_today/_days_ago/
_trained_at_sql` (subiram de `VolumeService`, cache por instância); em `analytics.py`, um
`_user_today` só (havia uma cópia mais abaixo, usada pelas rotas de progressão por grupo).

**Migrados**:
- `trained_at`: `volume_service` (janela e histórico), `recovery_read_model` (histórico
  empacotado e séries da janela), `muscle_summary_service` (sessões por grupo e drill-down),
  `adherence_service` (lista e detalhe de sessões), `report/daily_breakdown` (sessões do ciclo);
  `technique_volume.technique_sets_sql` perdeu a coluna (nenhum consumidor a lia).
- `CURDATE()`: `analytics.py` (15: resumo, tendências, grupos, progresso, RPE, cardio, overview,
  séries do exercício), `volume_service.py` (7, inclusive o `bucket_index` semanal),
  `recovery_read_model.py` (2), `intelligence/progression_service.py` (2),
  `training_load_service.py` (7 — semana civil `WEEKDAY(CURDATE())` virou `hoje − weekday()` do
  usuário; no domingo à noite a semana inteira era a seguinte), `periodization/logs.py` (limite
  diário de geração: `created_at >= início do dia do usuário em UTC`).
- `date.today()`/`datetime.now()`: `generation/activation_window.py` (`realized_activation` usa o
  hoje do usuário; `projected_slot_ids` exige `today`), `generation/mode_a.py` (dia do slot sem
  `plan_date` = hoje do usuário, resolvido uma vez), `generation/deterministic_prescription.py`
  (`now` = parede do usuário, mesma base de `last_executed_at`), `domain/prescription.py`
  (`frozen_at` default = carimbo UTC explícito; é instante de auditoria, não dia).
- Vigências por data (causa do teste instável): `template_assignments._as_moment(data)` = fim do
  dia do usuário no relógio do banco (era 23:59:59 comparado com `NOW()` UTC);
  `training_type_targets._as_moment` delega a ele (era cópia); `template_versions.plan_version_expr`
  usa `user_day_end_db_sql` (`plan_date + 1620 min` em São Paulo, era `+ 1 DAY`);
  `muscle_summary_service.resolve_template_plan` usa `_as_moment` (era `datetime.max.time()`);
  `segment_window` lê o dia da troca no fuso do usuário.

**Teste instável** `test_leitores_por_versao_pr3.py::test_com_a_flag_desligada_os_leitores_mostram_a_vigente`:
a v2 nasce de `NOW()` (UTC); entre 00:00Z e 03:00Z isso já é "amanhã" no relógio do banco, e o fim do
dia `HOJE` (do usuário) era comparado como `HOJE 23:59:59` — a janela que termina hoje lia a v1. A
semântica certa é a do dia do usuário (uma edição às 22h de Brasília é daquele dia), então a correção
é no código. Regressão determinística, a qualquer hora:
`test_edicao_entre_21h_e_24h_de_brasilia_ja_vale_para_o_dia_do_usuario` (congela `db_now` às 22h30 de
Brasília; falha com a regra antiga nos dois backends). `test_medicao_dataset_pequeno.py` deixou de
ancorar a janela em `CURDATE()` do banco (`_dias_ancorados_no_seed` saiu): a recuperação conta do hoje
congelado do usuário.

**Fica fora (não é motor)**: `CURDATE()` em cardio/Intervals/composição corporal (o agente de
cardio virou motor em 2026-10-08, IA-06, mas só pelos NÚMEROS do prompt em `domain/cardio_policy.py`;
o contexto/janela de dados continua fora de `MODULOS_DE_MOTOR`)
(`cardio_context_service`, `intervals_correlation_service`, `intervals_integration`,
`cardio_recommendations`, `body_composition_trend_service`) — mesma correção quando esses módulos
entrarem na lista de motores. Limite conhecido: o deslocamento é o do instante atual (em fuso com
horário de verão, linhas do outro lado da troca erram 1h; São Paulo não tem desde 2019) e as
vigências usam o fuso default (todos os usuários são de São Paulo; multi-fuso exigiria passar o fuso
do leitor até `_as_moment`/`plan_version_expr`).

**Impacto em produção** (leitura, 2026-10-07): 73 atividades nos últimos 90 dias; 17 sem
`started_at` (todas também sem `ended_at`); **2** mudam de dia com o `created_at` convertido (ids 7 e
51, lançadas às 23h34 e 22h54 de Brasília) e as 17 passam a cair no próprio `local_date` (antes 2
divergiam); 0 atividades com `started_at` mudam (o `started_at` já é hora local: 0 divergências com
`local_date`). Gerações de periodização nos últimos 30 dias: 7, **0** entre 21h e 24h de Brasília
(máximo 2 por dia; o limite é 10).

## 13. Lote 3a (2026-10-07) — e1RM com um dono só

Resolvidos: linhas **A** "e1RM Epley + RIR" e "RIR = 10 − RPE; defaults sem RPE", e **C12**.
Allowlists: `epley` 3 → 0 (`analytics.py` 1, `exercise_performance.py` 2), `rir` 2 → 0
(`analytics.py` 1, `exercise_performance.py` 1); padrão novo `dose_sem_rpe` (nasceu vazio).

**Dono**: `api/app/domain/e1rm.py` (puro). `estimated_1rm(carga, reps, rpe, missing_rpe=SKIP)`
(Epley + RIR, `None` sem carga/reps positivas ou sem RPE em `SKIP`), `reliable_e1rm` (≤
`E1RM_MAX_RELIABLE_REPS` = 12), `rir_from_rpe`, `resolve_rir`, `epley_multiplier`,
`MissingRpePolicy {SKIP, RIR_ZERO}`, `dose_inputs` + `DOSE_REPS_WHEN_MISSING` (8) /
`DOSE_RPE_WHEN_MISSING` (7,0). `MuscleRecoveryService.estimated_1rm`/`estimate_rir` só delegam
(mantêm o contrato antigo `0.0`); `metrics_registry.THRESHOLDS["e1rm_max_reliable_reps"]`
reexporta a constante.

**Predicado de confiabilidade** (COD-04, 2026-10-08): `is_reliable_reps(reps)` é do dono; `analytics._e1rm_reps_reliable` e `exposure_service.e1rm_reliable` são wrappers finos que o chamam (sem `reps` → `False`). Guarda: `e1rm_confiavel_paralelo`.

**Política sem RPE** (decisão de 2026-10-06, "meio-termo"): `SKIP` em todo lugar (sem RPE → sem
e1RM), **exceto** Desempenho por grupo (`RIR_ZERO`). Os textos passaram a dizer isso
(`metrics_registry` SETS_WITHOUT_RPE, `exercise-progress.ts` `e1rm_unavailable_without_rpe`,
`report_ai._PROGRESSION_RULES` — o "12" agora é interpolado de `E1RM_MAX_RELIABLE_REPS` —,
`PLANO_NOVO_DASHBOARD` linha `e1rm`).

**Migrados**:
- `analytics._fetch_group_e1rm_rows`: `_SQL_RIR` e o Epley em SQL saíram. Continua UMA consulta
  (`UNION ALL`): parte `set` traz a MAIOR carga por (janela, exercício, equipamento, convenção,
  reps, RPE) — redução que não depende da fórmula —, parte `contribution` traz os grupos dos
  exercícios da janela; o e1RM é calculado em Python por `reliable_e1rm(…, RIR_ZERO)`. Cruzar
  (reps, RPE) com os grupos no SQL multiplicaria o payload da Data API (usuário 1, 90 dias: 487
  linhas antes, 2.188 cruzando, 658 sem cruzar). Contrato de `build_group_performance` igual.
- `exposure_service`: e1RM pelo dono; **recorde `best_e1rm` só com série ≤ 12 reps** (era o maior
  Epley de qualquer série). O `e1rm` da exposição continua o maior de todas as séries (a regra de
  progressão trava por `e1rm_reps`). `paired_top_reps` usa o de `comparability`.
- `exercise_performance` (C12): `_same_load`/`_top_load_reps`/`_paired_top_reps`/`_rir` apagados;
  importa `comparability.paired_top_reps`/`e1rm_is_decisive` (agora públicas, com
  `top_load_reps`); a magnitude por reps usa `e1rm.epley_multiplier`.
- `volume_service`: melhor e1RM da sessão (base do `load_spike_factor`) só com séries de TRABALHO
  (`is_working_set`: sem técnica, backoff, aquecimento) e só convenção com `supports_e1rm`; dose e
  volume por split leem `dose_inputs`/`DOSE_RPE_WHEN_MISSING`.
- `services.compute_exercise_stats`: `best_weight_kg` e `avg_rpe` sem aplicações de técnica
  (`is_technique_application`); contagens, reps, volume e `min_weight_kg` iguais. Vale para o que
  for gravado/regravado daqui em diante (não há backfill).

**Impacto em produção** (leitura, 2026-10-07, usuário 1):
- Desempenho por grupo: mesmo resultado — consulta antiga × nova + Python numa janela 28 × 28 dias:
  470 linhas, **0** divergências; payload 711 linhas (~92 KB).
- Recorde de e1RM por chave (exercício + equipamento + convenção, séries de trabalho com RPE, 90
  dias): 161 chaves, **87 mudam** — 23 ficam sem recorde de e1RM (nenhuma série ≤ 12 reps com
  RPE) e 64 baixam (mediana −6,0%; de −0,4% a −72,5%; ex.: extensão lombar 107,8 → 58,7 kg,
  supino inclinado na máquina 45,7 → 26,4 kg).
- `load_spike_factor`: histórico inteiro (549 exercício×sessão, 75 sessões; 60 séries de técnica):
  2 itens mudam o melhor e1RM, **0** sessões mudam o fator.
- `compute_exercise_stats`: 549 exercícios gravados, 31 com técnica; **4** mudariam
  `best_weight_kg` e 2 o `avg_rpe` se regravados.

## 14. Lote 3c (2026-10-07) — pesos por papel e rótulos com um dono só

Resolvido: a linha **A (pesos por papel)** e **C13**. Allowlist `peso_secundario` 7 → 0 em 5
arquivos; padrão novo `rotulo_progressao` (1 entrada permanente).

**Pesos por papel.**
- Escrita: `muscle_contribution_resolver.DEFAULT_WRITE_WEIGHTS` (PRIMARY 1,0 · SECONDARY 0,5 ·
  STABILIZER 0,25; vale para estímulo e fadiga) — os mesmos do seed `001_schema.sql` (1,00/0,50) mais o
  STABILIZER que só o admin FE tinha. Consomem: `exercise_mapping_catalog` (formulário legado),
  o fallback estrutural de `VolumeService._resolve_contributions` e o admin FE
  (`lib/muscle-contribution-weights.ts`, lido de `motor-constants.generated.json`).
- Leitura: a tela Tendência (`/analytics/muscle-groups-mapped-trend|-table|-health|-debug`) deixou de
  aplicar PRIMARY 1,0 e só o 1º SECONDARY × `secondary_factor` 0,5 (parâmetro removido da rota e do FE).
  O SQL devolve o exercício canônico e `aggregate` pondera cada grupo pelo `stimulus_weight` cadastrado
  (PRIMARY, SECONDARY e STABILIZER), resolvido por `VolumeService` (uma leitura por requisição). "Incluir
  grupos secundários" passa a somar SECONDARY **e** STABILIZER. `sets`/`rpe_sets` seguem contagem física;
  grupo em dois papéis do mesmo exercício vira uma entrada (pesos somados).
- Política única para exercício **sem contribuição cadastrada**: a do `VolumeService` — a série conta
  como PRIMARY (peso 1,0) em "Outros", sem `muscle_group_id`. `_resolve_muscle_contributions_for_mappings`
  devolvia `[]` (o exercício sumia da ativação muscular e das séries equivalentes por atividade); a
  Tendência mandava para `unmapped` só. Agora os três seguem a mesma regra; a Tendência continua listando o
  exercício em `unmapped` (associação). Quem agrega por `muscle_group_id` (reforço ABC, Modo A) ignora o
  id `None`, então o motor de geração não muda.

**C13.**
- Rótulos: `domain/progression_status_labels.EXERCISE_PROGRESSION_LABELS` (com acento; em `domain/`
  para o briefing não importar o pacote `intelligence`). `metrics_registry` e `exercise_briefing` derivam
  dele; o FE (`dashboard-status.ts`, `exercise-progress.ts`) lê de `motor-constants.generated.json`. A home
  do dashboard recebia "Estavel"/"Suspeita de plato"/"Nao comparavel" da API — agora com acento.
- `keys_match` no FE: `comparability.mismatch_with_current` (sobre `keys_match`) e `comparison_with_current`
  publicam `comparable_with_current`/`mismatch_with_current` em cada exposição do drill-down
  (`ExposureOut`); `exercise-progress.ts` só lê o campo (`comparabilityMismatch(exposure)`,
  `buildRpeSeries(exposures)`).

**Impacto em produção** (leitura, 2026-10-07, usuário 1, 90 dias, métrica volume, média semanal):
- Contribuições ativas: 76 SECONDARY, **59 com `stimulus_weight` ≠ 0,5** (44 delas em exercícios usados
  nos 90 dias); 117 STABILIZER em exercícios usados (antes ignorados pela Tendência); 1 PRIMARY com 0,80.
- Padrão da tela (só primários): muda **só "Costas meio"** (2.398 → 2.286, −4,7%, o PRIMARY 0,80).
- Com secundários: Ombros/Deltoides +45,6%, Posterior de coxa +35,8%, Lombar +33,2%, Tríceps +32,1%,
  Glúteos +14,8%, Antebraço +13,9%, Panturrilha −14,2%, Flexores do quadril +63,7%, Manguito rotador
  +401,5% (212 → 1.064, quase todo de estabilizador), e **Core aparece** (0 → 3.697: só tinha
  contribuição SECONDARY/STABILIZER). Quadríceps/Costas/Bíceps ±1%.
- Exercícios sem contribuição usados em 90 dias: **0** (80 exercícios distintos, todos com contribuição;
  nenhuma série sem exercício canônico) — a política "Outros" não muda nenhum número hoje.

## 15. Lote 3b (2026-10-07) — série de trabalho e recorde com um dono só

Resolvidas as linhas **A (série de trabalho / preparatória / aplicação)** e **A (recorde)**. Dois
padrões novos na guarda, ambos com allowlist vazia: `set_type_paralelo` (11 ocorrências em 6
arquivos na `origin/develop` → 0) e `recorde_paralelo` (1 → 0).

**Série de trabalho (`set_types.py`)**
- `sql_not_preparatory` passou a listar os apelidos (`warm-up`, `warm_up`, `feeler_set`), a mesma
  resposta de `is_preparatory_set`.
- `sql_canonical_set_type_case(coluna, codes=None)` — `CASE` que devolve o canônico (ou um código
  curto). `recovery_read_model` usa ele em vez do `CASE` próprio (que mandava `drop-set`/`warm-up`
  para "working"); `SET_TYPE_CODES` sai do vocabulário do dono.
- `is_linked_application` (linha `working` com `technique_code` + âncora, exceto FST-7/GVT) mudou
  do motor de progressão para o dono; `progression_engine` importa.
- API: `activity_detail` e o detalhe de exercício devolvem `set_type` canônico (os demais
  endpoints de séries já normalizavam).
- FE: **um** `frontend/src/lib/set-types.ts` (`normalizeSetType`, `isWorkingSetType`,
  `isPreparatorySetType`, `isTechniqueApplicationSetType`) lendo `set-types.generated.json`,
  exportado por `api/tests/unit/test_m76_lote3b_set_types_fe.py --gravar` (o pytest falha se o JSON
  divergir do dono; `set-types.test.ts` roda o espelho contra os casos do JSON).
  `workout-mode-types.ts`, `exercise-set-edit.ts`, `workout-mode-technique.ts`,
  `workout-mode-previous-session.ts`, `DashboardMusculosClient.tsx` e `WorkoutDetailClient.tsx`
  consomem. `backoff` deixou de ser série de trabalho na "sessão anterior" do Modo Treino.

**Recorde (`intelligence/dashboard/session_records.py`, decisão 3)**
- `record_events(history)` é A regra: carga direcional estritamente maior que todas as exposições
  anteriores da mesma chave (exercício + equipamento + convenção); 1ª exposição não é recorde;
  empate de carga com mais reps não é recorde; sem equipamento resolvido não há recorde (fora da
  exceção do §8.1); `bodyweight` não tem recorde de carga. `record_exercise_ids` (dashboard) é
  `record_events` agrupado por sessão; `current_record(history, key)` é o recorde vigente da chave;
  `key_for` monta a chave de um escopo pedido com a mesma exceção do §8.1; `record_set_reps` dá as
  reps da série recorde (só informação).
- Modo Treino: `domain/equipment_record.current_record` + `record_payload`;
  `GET /exercises/last-session-sets` lê `HISTORY_SQL` do exercício (sem a sessão atual) e as séries
  só da exposição recorde. `record_scope` = `equipment` quando a chave tem equipamento. O e1RM do
  card fica em `summarize_equipment_history`, que chama `domain/e1rm.reliable_e1rm` (dono do Lote 3a); sem `equipment_id` pedido, o escopo é o equipamento da sessão anterior.
- Relatório/IA: `progression_service.get_recent_progression` (`MAX(best_weight_kg)` por nome) virou
  `get_recent_records` (60 dias, `RECENT_RECORDS_DAYS`), payload `recent_records` no lugar de
  `progression_highlights`; o prompt interpola a janela do dono; a tabela do snapshot da IA em
  `/dashboard/treinos` passou a "Recordes de carga (60 dias)".

**Impacto em produção** (leitura, usuário 1, 2026-10-07): 0 séries `backoff`, 0 apelidos e 0 linhas
"aplicação vinculada" no banco inteiro — a parte de série de trabalho não muda número nenhum hoje.
Recorde do Modo Treino, simulado em cada uma das 385 exibições dos últimos 90 dias que tinham
histórico: 157 iguais, 35 com outras reps (desempate por reps saiu), **193 deixam de mostrar
recorde** (158 porque a 1ª exposição ainda detém a marca, 34 sem equipamento resolvido, 1 peso
corporal), **0 novos** e 0 com carga diferente. Estado de hoje: das 131 chaves com carga, 58 mostram
recorde e 73 não (44 com uma exposição só).

## 16. Lote 3d — decisões de tela 2026-10-07

Duas decisões do usuário sobre o que a tela mostra quando a regra do dono (Lotes 3a/3b) **não**
produz número. Nenhuma regra nova fora do dono: os dois casos moram ao lado do dono de cada
regra, e o frontend só escolhe o rótulo. A guarda não muda (`recorde_paralelo`, `epley` e `rir`
continuam com allowlist vazia). Decisão 3 (tendência com estabilizadores): mantida, nada a fazer.

**1. Modo Treino — "Melhor carga" quando não há recorde**
- Dono: `intelligence/dashboard/session_records.py`. `best_load(history, key)` = a maior carga
  direcional já registrada na chave (exercício + equipamento + convenção), com as **mesmas**
  restrições de `record_events`: chave sem equipamento resolvido (fora da exceção do §8.1) →
  `None`; `bodyweight` → `None` (não se inventa "recorde de reps"); `assisted` é inverso (menor
  assistência). A exposição que detém a marca é a **primeira** que atingiu a carga (empate depois
  não toma a marca — a mesma semântica do recorde). `current_mark(history, key)` devolve
  `("record", RecordEvent)` quando há recorde vigente, senão `("best_load", BestLoad)`, senão
  `None`. `record_set_reps` aceita os dois (reps da série na carga da marca, só informação).
- `domain/equipment_record.current_mark` aplica o escopo do Modo Treino (sem a sessão atual,
  `key_for`); `record_payload(event, sets, kind)`.
- Contrato (`GET /exercises/last-session-sets`): `record` ganhou `kind: "record" | "best_load"`
  (`{"reps", "weight_kg", "kind"}`); `record: null` continua significando "nada a mostrar" (sem
  equipamento resolvido, peso corporal, sem carga). Resposta antiga sem `kind` = recorde.
- FE: `workout-mode-previous-session.formatPreviousSessionRecordLines` troca só o rótulo —
  "Recorde nesse equipamento: 8 x 32,5kg" × "Melhor carga nesse equipamento: 10 x 30kg"
  (assistido: "… de assistência").

**2. "Melhor 1RM estimado" — e1RM "não confiável (>12 reps)" quando não há confiável**
- Dono: `domain/e1rm.py` (`estimated_1rm` sem o teto, `is_reliable_reps`). Em
  `exposure_service._summarize_sets`, cada exposição passa a ter também `unreliable_e1rm` (maior
  e1RM das séries com **mais** de `E1RM_MAX_RELIABLE_REPS` reps, ainda exigindo RPE — política
  `SKIP`). `_records`: havendo e1RM confiável na chave, `best_e1rm` é só ele (`reliable: true`);
  sem nenhum, `best_e1rm` traz o maior não confiável com `reliable: false`; sem nenhum dos dois,
  `null`. `max_reliable_reps` vai junto (interpolado do dono), para a tela não escrever o 12.
- Contrato (`GET /analytics/dashboard/exercises/{id}/progress`): `records.best_e1rm` ganhou
  `reliable: bool` e `max_reliable_reps: int` (`ExerciseRecordOut`, opcionais; `best_load_kg` não
  usa).
- FE: `exercise-progress.e1rmReliabilityNote` → `RecordsCard` mostra o valor e, ao lado, a marca
  "não confiável (>12 reps)" quando `reliable === false`.

**Impacto em produção** (leitura, usuário 1, 2026-10-07; mesmas consultas dos Lotes 3a/3b):
- Modo Treino, por chave (histórico inteiro): 161 chaves com carga; 58 com recorde (inalteradas);
  103 sem recorde → **73 passam a mostrar "Melhor carga"** (todas as chaves comparáveis sem
  recorde; 44 delas com uma exposição só) e 30 continuam sem nada (sem equipamento resolvido).
  1 chave `bodyweight`, sem nada. Card de hoje (chave da sessão anterior de cada um dos 80
  exercícios): 42 "Recorde", **37 "Melhor carga"** (antes: nada), 1 peso corporal sem nada.
- "Melhor 1RM estimado" (90 dias): 161 chaves com algum e1RM; as **23** sem e1RM confiável
  passam **todas** a mostrar o maior e1RM de série > 12 reps marcado "não confiável"; as 138
  confiáveis não mudam. Na chave atual de cada exercício (o que a tela abre): 79 exercícios com
  e1RM, **11** mostram "não confiável".

## 17. Lote 4a (2026-10-07) — normalização semanal com um dono só

Resolvidas **E1, E2, E11 e E12**. Guarda `normalizacao_semanal`: 7 ocorrências em 5 arquivos → **1**
(`dashboard/body_service.py`, pesagens por semana — permanente).

**Regra (decisão do usuário na Fase 7, 2026-09-29):** a semana de um Modelo é **sempre** a janela de
7 dias a partir do 1º slot de treino; ciclo maior corta o excedente, ciclo menor dá a volta, descanso
ocupa o dia; frequência = nº inteiro de sessões na janela.

**Dono — `intelligence/methodology_volume.py`**
- `evaluation_window_days(slots, days)` (já existia): os slots da janela, dia a dia.
- `weekly_normalization(slots, values_by_sequence)`: "valor por slot do ciclo" → **planejado da semana**
  (cada slot de treino soma tantas vezes quantas cai na janela; descanso nunca soma).
- `calendar_weekly_rate(total, dias)`: acumulado em N dias de **calendário** → taxa semanal (só para o
  realizado numa janela corrida; o planejado de Modelo nunca usa).
- `weekly_window_days()`: os dias da semana (7) para quem precisa só do número.
- `block_of_group` / `block_volume_class_id`: fatia → bloco raiz, a mesma cadeia de `_resolve_block`.

**Consumidores**
- Geração Modo A (`generation/activation_window.py`, `mode_a.py`): janela móvel sempre de
  `weekly_window_days()` dias terminando no dia do slot; meta semanal **sem** escala (`target_scale`
  fica 1.0 só pelo contrato da auditoria). Antes: `max(7, ciclo)` dias e meta × ciclo/7.
- `/dashboard/musculos` (`muscle_summary_service`): `weekly_rate` e a meta do Modelo
  (`template_plan_weekly_targets(ativação do ciclo, dias do ciclo)`) delegam a `calendar_weekly_rate`.
  **Dois "planejados", duas réguas, um dono:** contra o REALIZADO (taxa de dias corridos) o Modelo entra
  no **ritmo do calendário** (`calendar_weekly_rate(ciclo, dias do ciclo)`); a **semana do editor**
  (`weekly_normalization`) é a régua da METODOLOGIA (builder, preview, `/dashboard/ciclo`, geração).
  Usar a semana do editor contra o realizado deixava a aderência perfeita a um ciclo de 8 slots 12,5%
  abaixo do planejado para sempre (fora da tolerância de 5%) — medido na revisão do PR #410 e revertido
  (teste `test_aderencia_perfeita_a_um_ciclo_de_8_slots_fica_no_planejado`). Projetado:
  `template_activation_by_slot` (abaixo) — Modo A deixou de projetar 0.
- `/dashboard/ciclo` (`analytics.dashboard_cycle`, E2): realizado = últimos 7 dias
  (`calendar_weekly_rate`); "Projetado" = **planejado da semana** (`template_activation_by_slot` +
  `weekly_normalization`, também em `planned_weekly_sets`), na mesma unidade do realizado; status =
  `classify_weekly_volume_status` do planejado da semana (`status_basis: "planned_weekly"`); o que falta
  no ciclo corrente fica em `pending_cycle_sets`, sem status.
- `template_activation_by_slot(template, template_plan, targets)` (em `muscle_summary_service`): valor
  por slot único dos dois dashboards — Modo B pela ativação ponderada da versão vigente; Modo A pelo
  ponto médio do orçamento do item (`project_muscle_item_budgets`, a regra do editor), `sets_count` só
  sem meta.
- `analytics._build_muscle_status_items` e `VolumeService.get_volume_breakdown`: `calendar_weekly_rate`.
- E11: `periodization/activation_projection.py` classifica com `classify_weekly_volume_status` (só
  `BELOW_MIN`/`ABOVE_MAX` viram warning) e não escala mais a meta por `window_days/7` (o preview já é a
  semana; a escala valia 1 desde a Fase 7).
- E12: `markdown_report_service.resolve_macro_groups` usa `block_volume_class_id` (o macro-grupo da RAIZ,
  como o motor; antes parava no 1º nó com classe). `size_class` saiu do `GET/POST/PUT /muscle-groups`
  (não é lido, não é gravado, campo de cliente antigo é ignorado). **Pendente (contract):** dropar a
  coluna `muscle_groups.size_class` e o eco `size_class` no payload da metodologia.

**Impacto em produção** (leitura, 2026-10-07): os 6 Modelos ativos têm 8/4/8/7/8/8 slots (o "24" do
plano era a contagem de slots somando versões). `/dashboard/musculos`: **nenhuma mudança** de meta nem de
status — linhas cruas de produção (usuário 1, Modelo 28, janelas de 28 e 90 dias, com as aplicações de
técnica) rodadas no código da develop e deste lote dão payloads iguais. Geração Modo A: só o Modelo 9
(8 slots, sem usuário) muda (janela 8→7 dias, meta ×8/7→×1). `/dashboard/ciclo`: usuários 4 e 5 (Modelo
24, 7 slots) sem mudança de status (a coluna passa a mostrar o planejado da semana); usuário 1 sem
cobertura (Modelo sem Tipo de Treinamento).

## 18. Lote 4b (2026-10-07) — ativação com técnica e status do backend nas telas

Resolvidas **E3, E4, E5 e E6**. Três padrões novos na guarda: `payload_ativacao_sem_tecnica`
(sobra 1 permanente), `limiar_ativacao_fe` e `metodologia_no_fe` (vazias). Nenhuma escrita em
produção; sem migração.

**E3 — payload único de ativação**
- Dono: `technique_volume.activation_payload(item)` = `{mapping_id, sets_count,
  advanced_technique, advanced_technique_applications}` (o shape do item do Modelo). As séries
  com técnica são `planned_item_sets` (Fase 5), calculadas DENTRO do VolumeService — quem chama
  nunca pré-soma.
- `MuscleActivationExerciseIn` (entrada de `/analytics/muscle-activation-preview` e
  `/muscle-activation-breakdown`) aceita os dois campos de técnica;
  `get_muscle_activation_breakdown_by_exercise` passou a somar o equivalente (o preview já somava).
- Consumidores: ficha (`MuscleActivationPreview.tsx`, `ExercisesTable.tsx` via
  `lib/activation-payload.ts`), resumo do Modo B (`mode_b.py`), `mode_b_legacy.py`,
  `mode_a_legacy.py` e `template_volume_validator.calculate_slot_activation`.
- Exceção permanente da guarda: `workout_plan_templates._activation_input` manda
  `sets_count = planned_item_sets(...)` (já com a técnica) — mesma regra, sem cópia.

**E4 — alvo do slot em séries equivalentes e status no backend**
- `mode_b.planned_slot_targets`: `template_slot_targets[].sets_count` = `total_equivalent`
  planejado da ficha por grupo (stimulus_weight × séries, com técnica e com a dose reduzida da
  recuperação já aplicada), a mesma unidade da ativação realizada da sessão. Antes: soma das
  séries brutas dos itens do grupo (sem técnica, sem secundários).
- `volume_service.assess_activation_against_target` / `assess_activation_rows`: cada linha de
  `GET /analytics/muscle-activation-by-activity` ganhou `assessment = {status, status_domain,
  target_source, target_sets, max_sets}`. Alvo do slot → `classify_planned_volume_status`
  (`plannedVolume`), e `ABOVE_MAX` quando passa do `effective_max_sets` (> 0); meta do tipo de
  treino → `classify_weekly_volume_status` com min/alvo/máx; sem alvo → `classify_weekly_volume_status`
  contra `DASHBOARD_FALLBACK_*_SETS`. `training_type_targets` ganhou `min_weekly_sets` e
  `target_source` (aditivos). Régua: compara UMA sessão com o alvo DAQUELE slot (mesma unidade,
  sem janela) — não há taxa semanal a normalizar aqui, então nem `calendar_weekly_rate` nem
  `weekly_normalization` (Lote 4a) entram; a meta do tipo de treino continua comparada como
  antes do lote (sessão × meta semanal).
- FE: `MuscleActivationTable.tsx` só desenha `statusVisual(status_domain, status)`; os limiares
  0,9/0,5/6/9 saíram. `buildDraftRawPayload` volta a gravar `template_slot_targets` na ficha salva
  (desde 2026-09-13 o save não gravava, e a tabela caía na meta genérica).
- `VolumeGrupamentosClient.tsx`: não tem status de meta (só a escala de cor do mapa de calor,
  normalizada pelo mínimo/máximo da própria grade) — nada a mudar.
- Dados existentes: sem backfill. Só 4 fichas salvas têm `template_slot_targets` do Modo B
  (94, 100, 103, 104, de 08 a 12/09); continuam com o alvo bruto antigo.

**E5 — séries do slot recolhido**
- `slotGroupSets` só agrupa (grupo principal, subgrupos, exercícios). `slotGroupSetsWithStatus`
  põe na linha o `direct_sets` da sessão do bloco no preview da metodologia (PRIMARY, com
  técnica) — o mesmo primeiro número do "9/12"; sem sessão (preview carregando, exercício sem
  PRIMARY no grupo), a linha mostra só os exercícios. Ordem por essas séries.

**E6 — metodologia do realizado**
- `GET /analytics/methodology-realized` (`intelligence/methodology_realized.py`): janela de 7 dias
  terminando no "hoje" do usuário; por BLOCO (fatia → bloco pela cadeia inteira, `block_of_group`),
  séries diretas por dia, `frequency_bucket_for`, faixa por treino e meta semanal do macro-grupo
  do bloco, e três status com `_compare`: o do dia (desenho da célula), o da **média por treino**
  (a leitura da metodologia) e o da semana — esta pela régua do REALIZADO do Lote 4a
  (`calendar_weekly_rate` sobre a janela de calendário; com 7 dias é o próprio total), nunca
  `weekly_normalization` (régua do planejado). Grupo com meta semanal e sem treino na janela fica
  `BELOW_RANGE` na semana (decisão de tela mantida; o motor do Modelo usaria
  `NOT_DIRECTLY_TRAINED`).
- FE: `muscle-activation-7d.ts` só traduz códigos e monta textos; `MuscleActivation7d.tsx` lê só
  o endpoint novo (sai a leitura de `/muscle-volume-classes` e `/muscle-groups` da tabela) e mostra
  a média por treino ao lado da frequência. Os pares de recuperação casam pela linha via
  `member_groups` do backend. O cenário do teste vira
  `frontend/src/lib/methodology-realized.fixture.json`, gerado por
  `api/tests/unit/test_m76_lote4b_ativacao_e_status.py --gravar` (o pytest falha se divergir).

**Impacto em produção** (leitura, usuário 1, 2026-10-07):
- E3: 36 linhas de fichas (em 16 fichas) têm técnica com equivalente > 0 (rest-pause ×2: 17;
  drop-set ×1: 8; ×2: 4; parciais ×3: 3, ×10: 2; rest-pause ×1: 2). Na prévia/breakdown da ficha,
  72 de 433 pares (ficha, grupo) mudam, de +0,02 a +2,3 séries equivalentes (+36,2 no total).
  O Modelo já contava a técnica (sem mudança).
- E4: nas 4 fichas do Modo B com alvo salvo, 13 de 16 alvos ficariam maiores em séries
  equivalentes (ex.: Glúteos 2 → 7,88; Bíceps 4 → 7,9; Quadríceps 8 → 10,3) — sem backfill, ficam
  como estão. Em `/registro-exercicios`, 691 pares (atividade, grupo) em 75 atividades: 636 usam a
  meta genérica, 38 o alvo do slot, 17 a meta do tipo de treino. **82 mudam de cor**: 53 verdes
  da meta genérica viram "Acima da meta" (≥ 9,45), 16 verdes do slot viram "Acima do planejado",
  7 amarelos viram "Na meta" (8,55–9), 4 amarelos do slot e 2 do tipo de treino viram vermelho
  (abaixo do planejado/mínimo). Os 472 vermelhos e 78 amarelos da meta genérica não mudam de
  faixa; o rótulo e o ícone de todos passam a ser os do dashboard. (Realizado aproximado por
  `sets_count × stimulus_weight`, sem a fatia de aplicações de técnica.)
- E5: no Modelo 21 (ABCDEF Avançado), 11 de 22 linhas do slot recolhido mudam (ex.: Quadríceps
  9 → 11,3, Ombros 8 → 9,8); nos Modelos 28 e 31, nenhuma.
- E6: produção não tem neto na árvore de grupos, então nenhuma célula nem coluna semanal muda.
  Novidade é a média por treino: na janela de 01–07/10, 13 blocos; 7 com dias de cores mistas e
  média na faixa (Costas, Bíceps, Quadríceps, Panturrilha, Ombros, Peitoral, Tríceps), Glúteos
  com média acima, Manguito rotador e Antebraço abaixo, Lombar sem faixa.

**E4 (complemento, decisão do usuário 2026-10-07) — "muito abaixo do planejado" volta, pelo backend**
- Com o Lote 4b a tabela perdeu o nível vermelho "muito abaixo" (o FE pintava assim quando o
  realizado ficava abaixo de 50% do alvo). O usuário decidiu devolvê-lo **no backend**: a regra
  única do status planejado ganha um nível de gravidade; o FE só pinta.
- Dono: `volume_service.FAR_BELOW_PLANNED_RATIO = 0.5` e `planned_volume_severity(actual,
  planned)` → `"FAR_BELOW"` quando `actual < planned × 0,5`; `None` nos demais casos e sempre com
  planejado 0 (nunca é déficit). É **aditiva**: o status continua `BELOW_TARGET` e os 3 estados de
  `classify_planned_volume_status` (`PLANNED_VOLUME_STATUSES`, legenda, paridade) não mudam. Como
  0,5 < 1 − `ON_TARGET_TOLERANCE_PCT`, todo `FAR_BELOW` já é `BELOW_TARGET`.
- `assess_activation_against_target`: `assessment` ganha `severity` (aditivo; `null` quando não
  se aplica). **Escopo: só o alvo do slot** (`template_slot`, domínio `plannedVolume`). A meta do
  tipo de treino (5 estados com mínimo — `BELOW_MIN` já é o vermelho) e a meta genérica ficam
  como estão, sempre `severity = null`; passar do teto do slot (`ABOVE_MAX`) também.
- FE: `dashboard-status.plannedSeverityVisual('FAR_BELOW')` = vermelho, "Muito abaixo do
  planejado", ícone de alerta; `MuscleActivationTable.tsx` usa esse visual quando
  `assessment.severity` vem e o de `statusVisual` nos demais casos — sem número nenhum.
- Cor **só nesta tabela** (decisão do usuário 2026-10-07: o vermelho volta a significar "muito
  abaixo"): `plannedVolume` + `BELOW_TARGET` sem gravidade = **amarelo** ("Abaixo do planejado");
  `FAR_BELOW` = **vermelho** ("Muito abaixo do planejado"). O mapeamento mora em
  `MuscleActivationTable.activationVisual` (código de status/gravidade → cor). O visual
  compartilhado de `dashboard-status.ts` — `BELOW_TARGET` vermelho em `/dashboard/musculos` e no
  mapa corporal — não muda; `BELOW_MIN` (meta do tipo de treino/genérica) segue vermelho.
- Outros consumidores de `classify_planned_volume_status` (`/dashboard/musculos`,
  `muscle_summary_service`, Modelo `equipment_series`): **não recebem o campo** neste PR. Lá a
  régua é semanal e o contrato do item (`MuscleSummaryItem`) é fechado; se um dia quiserem o nível,
  chamam `planned_volume_severity` (sem nova constante).
- Guarda: padrão novo `muito_abaixo_planejado` (constante `FAR_BELOW*`/`MUITO_ABAIXO*` = 0,5 ou
  conta `planejado/alvo × 0,5`), dono `volume_service.py`, allowlist vazia. O `limiar_ativacao_fe`
  continua vazio (o FE não volta a ter 0,9/0,5).
- **Impacto em produção** (leitura, usuário 1, 2026-10-07): 11 fichas têm `template_slot_targets`
  (75, 82, 85, 87, 88, 90, 93, 94, 100, 103, 104), cada uma ligada a 1 atividade. Dos 38 pares
  (atividade, grupo) com alvo do slot que aparecem na tabela (21 acima, 11 no planejado, 6 abaixo),
  **1 passa a "Muito abaixo do planejado"**: atividade 199, Lombar 1,2 de 5,2 (23%). Os outros 5
  "abaixo" ficam entre 50% e 94% (Peitoral da 179 está exatamente em 50%, fica "Abaixo"). Outros
  20 grupos com alvo do slot e realizado 0 seriam "muito abaixo", mas não aparecem na tabela (ela
  só lista grupos ativados na sessão). Fichas salvas desde o #411 gravam o alvo de novo, então o
  nível passa a aparecer nas sessões novas. (Realizado aproximado por `sets_count ×
  stimulus_weight`, sem a fatia de aplicações de técnica, como na medição do Lote 4b.)

## 19. Lote 4c (2026-10-07) — tempo de treino, defaults e tetos com dono; prompts interpolados

**E7 — Tempo de treino do Modelo.** O dono passou do TS para o backend:
`periodization/time_estimate.estimate_slot_time(items, default_rest_seconds)` devolve o resultado
completo (`total_seconds`, `breakdown` com aquecimento/ajuste/trabalho/técnica/cardio/execução/
descanso, `items_without_rest`, `has_estimated_reps`). `POST /workout-plan-templates/preview-activation`
(modo `equipment_series`) passa a devolver `slot_times[]` (um por slot do ciclo, não só da janela):
`{slot_sequence, estimated_seconds, breakdown, items_without_rest, has_estimated_reps}`.
O builder mantém o **espelho** `template-time-estimate.ts` porque o número muda a cada tecla e o
preview só responde depois do debounce — sem constante própria (lê `motor-constants.generated.json`,
seção `time_estimate`) e com paridade **completa** (não só `totalSeconds`) contra
`template-time-estimate.parity.json`, ambos gerados pelo dono
(`api/tests/unit/test_m76_lote1b_espelhos_fe.py --gravar`). Decisões da Fase 7 preservadas:
tempo = descanso + execução; 3 s/rep; reps vazias = 10; sem descanso conta 0 s com a nota
"N sem descanso definido".

**Três descansos, três perguntas (não é divergência):**
| Pergunta | Valor | Dono |
|---|---|---|
| Quanto conta, na ESTIMATIVA da tela, um exercício sem descanso definido? | 0 s (e avisa) | `time_estimate.REST_WHEN_UNDEFINED_SECONDS` |
| Quanto conta, na conta dos 90 min do COMPLEMENTO da IA, a série sem descanso? | 90 s (teto do intervalo padrão; decisão de 2026-10-05) | `limits.COMPLEMENT_DEFAULT_REST_SECONDS` = `prescription_defaults.DEFAULT_MAX_REST_SECONDS` |
| Quanto a ficha GRAVA / o timer do Modo Treino usa sem descanso definido? | 60 s (ponto médio do intervalo padrão 30–90) | `prescription_defaults.default_rest_seconds()` |

**E8 — Defaults de prescrição.** `domain/prescription_defaults.py`: `DEFAULT_REPS_TARGET` "8-12",
`DEFAULT_RPE_TARGET` "8-9", `DEFAULT_MIN/MAX_REST_SECONDS` 30/90, `REST_SECONDS_SANITY_MIN/MAX`
15/240, `DEFAULT_SETS_COUNT` 3, `MIN_SETS_COUNT` 1, `midpoint_rest_seconds()` (o mesmo `round`
das 12 cópias) e `default_rest_seconds()`. Consumidores: `models.WorkoutPlanGenerateIn`,
`generation/mode_a.py`, `mode_b.py` (só o default de reps e o ponto médio do descanso),
`mode_a_legacy.py`, `mode_b_legacy.py`, `workout_plan_templates_engine.py`,
`template_ai_selector_v2.py`, `abc_volume_reinforcement.py`, `workout_plan_ai.py`, prompt da
Periodização. FE: `prescription-defaults.ts` (lê a seção `prescription` do JSON) em
`ExercisesTable.tsx` (placeholders), `GenerateDraftModal.tsx`, `ModeSelectScreen.tsx`,
`workout-plan-generate-payload.ts` e o timer do Modo Treino (`FreeformScreen`,
`PlanExecutionScreen`). Ficam `abc_workout_ai.py`/`abc_workout_template.py` (código morto, Lote 5).

**E9 — Tetos.** `periodization/limits.py` nomeia cada teto; nenhum número mudou. Os valores
divergem por motivo legítimo e a tabela do módulo diz qual pergunta cada um responde:
geração (prompt 6/8/12), complemento determinístico (9 exercícios, 6 séries, 90 min, 90 s),
priorizados (3: save do Modelo, validador da IA, prompt e builder via JSON), Modo A (3
exercícios por grupo), motor v2 legado (6 séries) e reforço ABC legado (12/9). Séries ideais por
exercício (3–4) continuam no dono de EXG-004 (`methodology_volume`). O "tipicamente 8-9" do
prompt é `SESSION_EXERCISES_TARGET_MIN`–`SESSION_EXERCISES_TYPICAL_MAX` (= teto do complemento).
Rótulos do relatório (`VOLUME_GAP_REASON_LABEL`, "passou de 4 séries") leem a seção
`periodization_limits` do JSON.

**E10 — Prompts/RAG.** Prompt da Periodização (`periodization-v8`): cobertura do Modo A de
`template_volume_domain` (90/110); exemplo da célula calculado da TABELA do próprio contexto
(célula 1x de maior piso; a faixa de exercícios só entra quando a célula a tem); exemplo de técnica
por `planned_equivalent` com o catálogo do contexto; limite de priorizados e "tipicamente 8-9" de
`limits.py`; exemplo de reps/RPE de `prescription_defaults`. RAG: `HIPERTROFIA.md` (HYP-025) e
`FULLBODY.md` remetem à meta semanal do macro-grupo em vez de "9 a 10 séries semanais". Legado
(`template_ai_selector_v2`, `templates_engine` v1): só o exemplo de reps do seletor foi
interpolado; o resto morre no Lote 5.

**Allowlist:** `defaults_prescricao` 26 → 8 (sobram `abc_workout_ai.py` 1 e
`abc_workout_template.py` 7, código morto). Padrões novos: `descanso_default` (nasce com 12, todas
em código morto), `tetos_sessao`, `premissas_tempo` e `numeros_prompt_volume` (nascem vazios).

**Impacto em produção** (leitura, 2026-10-07):
- Tempo total por slot dos 8 Modelos vigentes (58 slots): **igual** antes × depois — a fórmula não
  mudou (os 11 totais da fixture antiga continuam os mesmos e o teste trava isso). Ex.: Modelo 28
  "ABC-DEF": 3789/3798/3636/3765/3555/3642 s; Modelo 21: 2541/1929/2607/2700/1470/2713 s.
- Itens de Modelo vigente que caem em default: 90 de 200 itens de equipamento sem descanso
  definido (contam 0 s e mostram a nota; 88 deles nos Modelos 21 e 27);
  3 sem reps (contam 10) e 3 sem RPE.
- Fichas dos últimos 90 dias (390 exercícios): nenhuma sem descanso, reps ou RPE (a geração sempre
  preenche); 199 com descanso 60 s, 106 com reps "8-12", 38 com RPE "8-9" — os mesmos valores que o
  dono devolve, então nada muda no que é gravado.
- Tetos: nenhum valor efetivo mudou; 0 slots com mais de 3 itens priorizados.
- Prompt: em produção a tabela tem Grandes 1x = **16–20** séries e nenhuma faixa de exercícios (o
  admin cadastrou só séries); o exemplo do prompt v7 dizia "4-5 exercícios e 15-20 séries" (seed da
  057), contradizendo a tabela. No v8 o exemplo é "Grandes em 1x pede 16-20 series". O exemplo de
  técnica continua "4 + 0,8 = 4,8" (rest-pause 0,40 × 2 no catálogo de produção).

## 20. Lote 5a (2026-10-07) — código morto com regra paralela apagado (E14 parte 1)

O Lote 5 tem duas partes (decisão do usuário 2026-10-07). A parte 1 apaga só o que **não tem
chamador**; a parte 2 (o caminho legado ativado por `GENERATION_ENGINE=legacy`) espera 30 dias de
`generation.fallback = 0` a partir de 2026-10-07.

**Apagado:**
- `api/app/abc_workout_ai.py` e `api/app/abc_workout_template.py` (inteiros): geradores ABC
  (template fixo e IA). Ninguém os importa — nem `main.py`, `main_engines.py`, os handlers
  `lambda_*.py`, nem o caminho legado (`workout_plan_templates_engine`, `mode_*_legacy`,
  `template_ai_selector_v2`); o único importador do `abc_workout_template` era o próprio
  `abc_workout_ai` e o `generate_plan_draft` (morto). Carregavam cópias de "1-2 séries"
  (dose reduzida), `"8-12"`/`"8-9"`, descanso 30/90 e um "+5%".
- `api/app/workout_plan_ai.py`: `generate_plan_draft` (Full Body antigo), `build_prompt`,
  `PLAN_JSON_SCHEMA_HINT`, `_increment_text`, `load_rag_text`, `FULLBODY_*` (ordem/categorias,
  `FULLBODY_SETS_BASELINE_BY_DOSE`), `build_fullbody_slots`, `_hours_since_last_trained`,
  `MANDATORY_ROTATOR_*`/`build_mandatory_rotator_slots`, `deduplicate_by_group_and_name`,
  `_apply_slot_bounds`, e o que só eles usavam (`RAG_MARKDOWN_DIR`, `OPENAI_API_KEY`, o reexport
  `DENSITY_WINDOW_HOURS` e os imports de `prescription_defaults`, `progression_engine`,
  `effective_intensity`, `rag_storage`). 1.639 → 827 linhas. Fica tudo que os Modos A/B atuais,
  `services.py`, o planner e o caminho legado importam (`gather_ficha_context`,
  `project_recovery_for_date`, `reconcile_with_gym_catalog`, `assign_progression_indicators`,
  `assign_warmup_flags`, `normalize_load_target`, `resolve_gym_name`, `_internal_get` (removido
  no SEG-04, 2026-10-09: academias e catálogo lidos direto do banco),
  `WorkoutPlanGenerationError`, `call_openai`, `_extract_json`, `_strip_amazfit_fields`,
  `get_recent_fullbody_exercise_names_by_group`, wellness do intervals.icu).
- Testes exclusivos do morto: `test_recovery_exclusion_summary_groups.py` (helper e blocos dos
  geradores ABC) e `test_full_body_deriva_o_baseline_da_regra_unica` (Lote 2a); o
  `monkeypatch` de `load_rag_text` no teste de integração sem DDL (a função nunca era chamada).
- `WEIGHT_DRAG_STEP_KG`, `dashboard_alert_service`, `ExerciseSimilarityService`,
  `recommend_focus_groups`: já não existiam no código (Lote 2c e anteriores) — nada a fazer.

**Prova de não-uso:** grafo de import por AST (todo `from … import` e `módulo.atributo` em
`api/`, inclusive testes) + grep em `api/`, `frontend/src`, `infra/`, `scripts/`, `tools/`,
`.github/` e `docker-compose.yml` (só comentários históricos citam os nomes) + importação de todos
os entrypoints (`app.main`, `app.main_engines`, `app.lambda_*`) e do caminho legado. Produção
(CloudWatch Logs Insights, 14 dias de retenção, `musclefit-plan-generator`, `musclefit-api-core`,
`musclefit-engines`, `musclefit-workout-ai`; 94.421 eventos): **0** ocorrências de
`abc_workout`, `generate_abc_plan_draft`, `generate_plan_draft`, `build_fullbody_slots`,
`build_mandatory_rotator`; e **0** de `generation.fallback`/`GENERATION_ENGINE`.

**Fica para a parte 2 (alcançável pela flag `GENERATION_ENGINE=legacy`):**
`generation/mode_a_legacy.py`, `generation/mode_b_legacy.py`,
`intelligence/template_ai_selector_v2.py`, o reparo do `template_volume_validator`,
`workout_plan_templates_engine.py` v1 (com as constantes ABC 3/9/12) e
`abc_volume_reinforcement.py` (importado pelo `templates_engine` v1). Em `workout_plan_ai.py`, o
que hoje só esses módulos importam (ex.: `call_openai`, `_extract_json`, `_strip_amazfit_fields`,
`get_recent_fullbody_exercise_names_by_group`, wellness) sai junto na parte 2 se ficar sem chamador.

**Allowlist:** `dose_reduzida_texto` 6 → 0, `defaults_prescricao` 8 → 0, `descanso_default`
12 → 0, `percentual_de_carga` 1 → 0 (27 ocorrências em 2 arquivos; os quatro padrões ficam com
allowlist vazia).

**Impacto em produção:** nenhum (código sem chamador).

## 21. Fadiga prévia na sessão (2026-10-07) — PR 1 de 2

**Problema:** o mesmo exercício em ordens diferentes (supino 1º num dia, 5º noutro, depois de
exercícios que já cansaram primários/secundários/estabilizadores) era tratado como a mesma
condição. **Dono novo:** `api/app/domain/prior_fatigue.py` (Python puro, sem I/O).

**Fórmula:** músculo principal = PRIMARY de maior `stimulus_weight` (empate: menor
`muscle_group_id`, depois nome; sem PRIMARY, a de maior peso). Fadiga prévia = Σ nos itens
ANTERIORES de séries × `stimulus_weight` daquele músculo (o próprio item não conta). Nível =
`min(1, prévia / 9)`. Faixas: `< 2` descansado, `2 ≤ x < 6` pré-fatigado, `≥ 6` muito fatigado.
Unidade = séries equivalentes (a mesma da ativação); granularidade = grupo de
`exercise_muscle_contributions`.

**Medição (DEV com dados de produção do usuário 1, versão vigente dos 5 Modelos
`equipment_series`, 200 itens):** 1º item do grupo no slot (n=119): mediana 0, 78% < 2
(resíduos de secundário/estabilizador ficam abaixo de 2), 22% ≥ 2 (ex.: bíceps depois de
costas, 4,5; tríceps depois de peito e ombro, 8,25). Itens posteriores do mesmo grupo (n=81):
mínimo 2,0, mediana 3,95, P70 6,0, máximo 9,5. Corte 2 separa 100% dos posteriores dos
primeiros "limpos"; corte 6 ≈ dois exercícios diretos de 3 séries antes (31% dos
posteriores); saturação 9 deixa o vermelho pleno só no topo (~3%). Faixas resultantes:
93 descansados, 78 pré-fatigados, 29 muito fatigados.

**Entrega (PR 1):** motor + `item_prior_fatigue[]` no preview do Modelo (todos os slots do
ciclo, na ordem enviada; recalcula a cada reordenação/edição pela mesma query do preview) +
faixa de cor verde→amarelo→vermelho nos cards do editor (expandido e recolhido), com
`title`/`aria-label` "Fadiga prévia no <grupo>: <faixa>". **Guarda:** `fadiga_previa` (vazia).

**PR 2:** entregue — ver §22.

**Impacto em produção:** visual + campo novo aditivo no preview; nenhuma mudança de semântica de
progressão, volume ou recuperação.

## 22. Fadiga prévia na sessão (2026-10-07) — PR 2 de 2: comparabilidade e prescrição por contexto

**Problema:** o supino feito 1º na segunda (3×10×30 kg, RPE 9) e 5º na quinta, depois de
exercícios que já cansaram peitoral/tríceps/deltoide (6×20), eram uma sequência só: regressão
falsa no dashboard e a quinta prescrita com a carga da segunda.

**Contexto de fadiga** = a faixa do dono `prior_fatigue` (`context_band`: descansado /
pré-fatigado / muito fatigado, as do editor) sobre a sessão REAL (exercícios executados antes,
na ordem `exercises.sequence`, com as séries não preparatórias — técnica pela fatia de
`realized_set_units` — × `stimulus_weight` cadastrado). Derivado em leitura do histórico
registrado (retroativo, sem backfill). Grupos irmãos não se somam: só o cadastro de
contribuições decide.

**Comparabilidade** (`comparability.py`/`exposure_service.py`): `ComparabilityKey.fatigue_context`
entra na igualdade (`keys_match`); `physical_keys_match` ignora o contexto. O run comparável é
o do contexto da ÚLTIMA exposição: exposições de outro contexto intercaladas são PULADAS
(`trailing_comparable_indices`, motivo `other_fatigue_context_exposures_skipped`); troca de
equipamento/convenção continua interrompendo / `NOT_COMPARABLE`. Primeira exposição num
contexto = `INSUFFICIENT_DATA` com `first_exposure_in_fatigue_context`. Drill-down: cada
exposição traz `fatigue_context` (rótulo do dono) e `mismatch_with_current =
fatigue_context_changed` quando é de outro contexto; o status vem com o contexto da última
exposição ("Progresso · pré-fatigado"). Recorde e e1RM continuam absolutos (`_records` compara
a chave física). Leitura de contexto em lote (`session_fatigue_reader`: 1 leitura por 100
atividades + 2 do resolvedor), nunca por exposição; falha de leitura degrada para a chave sem
contexto com limitação declarada.

**Progressão** (`progression_engine`/`progression_evaluator`/`progression_state`): trilha por
`(exercise_id, equipment_id, fatigue_context)` (migração 068: coluna + UK nova). Execução num
contexto sem trilha cria a trilha em CALIBRAÇÃO (neutra) e grava `calibrated_at`; essa sessão
(e as anteriores) não entram no histórico de D10/platô da trilha (`counts_for_history`) — a
primeira exposição num contexto novo não dispara regressão/deload/platô. Prescrição: contexto da
posição PLANEJADA (`planned_fatigue_contexts`, mesmo dono, séries de `planned_item_sets`) no
Modo B, freeze A/B; sem histórico no contexto, `resolve_track` parte da trilha do par
executada mais recentemente (qualquer contexto), sem fator de desconto. Ação pendente com alvo
de outro contexto não se aplica (`target_matches`; alvo legado de 2 posições vale para o par).
A técnica da F4 continua por PAR (063).

**Limitações:** quem prescreve sem posição planejada (troca de equipamento no Modo Treino,
treino livre, card "Sugestão para a próxima sessão", linha acrescentada à mão, preview do Modo
A) usa a trilha do par executada mais recentemente; o Modo Treino não recalcula a sugestão
quando a ordem real muda na sessão (não há fluxo de recálculo por ordem — vale o planejado);
o sinal de desempenho da recuperação (`exercise_performance`, exposições montadas do
`VolumeService`) ainda compara sem contexto. O card legado `progression_signals_30d` saiu com
`/analytics/dashboard-overview` no COD-05 (§25).

**Guarda:** `trilha_sem_contexto` (vazia).

**Impacto em produção (medido no DEV com dados de produção do usuário 1, janela 90d,
80 exercícios):** ver a descrição do PR. Resumo: 20 status mudam; regressões 15 → 9 (5 viram
dados insuficientes, 1 vira progresso); progressos 21 → 16; dados insuficientes 11 → 27; 16 de
56 sequências comparáveis ficam curtas demais (< 2) com 3 faixas (9 de 56 com 2 faixas).
Replay do histórico (52 sessões): trilhas 122 → 188; DECREASE_LOAD 4 → 0; INCREASE_LOAD 2 → 0;
11 de 66 prescrições da próxima sessão mudam de carga (−13,3 a +10 kg). **Pós-deploy:** rodar
`db-migrations.yml action=replay` para separar as trilhas existentes por contexto (até lá
todas ficam "descansado" e os contextos novos nascem em calibração).

## 23. FC de repouso e HRV com origem manual (2026-10-08)

**Não é regra de motor nova**: muda só a **origem do dado** de `intervals_wellness.resting_hr_bpm` e
`intervals_wellness.hrv_ms`. O usuário pode digitar os dois valores de um dia em `/intervals-icu`
(`PATCH /integrations/intervals/wellness/{date}`); a origem fica em `resting_hr_source` / `hrv_source`
(`'intervals'` | `'manual'`, migração 069, default `'intervals'`) e `manual_updated_at` (auditoria).

**Precedência (dono: `app/intervals_store.py`, `upsert_wellness` → `_sync_precedence_assignments`)**,
por métrica, em todo caminho de sync (job diário, refresh do `/dashboard`, `POST /sync`, `POST /wellness`):
o Intervals.icu com valor não nulo sobrescreve e a origem volta a `'intervals'`; com valor nulo e
origem `'manual'`, a edição fica; com valor nulo e origem `'intervals'`, grava NULL (como antes). O
`null` explícito no PATCH limpa a edição (NULL + `'intervals'`). As expressões do `ON DUPLICATE KEY
UPDATE` não dependem da ordem das atribuições (teste de integração roda as duas ordens no MySQL).

**Faixas de entrada** (validação da integração, não regra de cálculo): `intervals_store.MANUAL_RESTING_HR_BPM_MIN/MAX`
(20–250 bpm) e `MANUAL_HRV_MS_MIN/MAX` (1–500 ms) → 422 fora delas; data futura (no fuso do usuário,
`time_windows.user_today`) → 422. O frontend não conhece as faixas (mostra a mensagem do backend).

**Consumidores** (`recovery_signals_service`, `report/daily_breakdown`, `intervals_correlation_service`,
`cardio_context_service` e demais leitores de `intervals_wellness`) continuam lendo `resting_hr_bpm`/`hrv_ms`
como antes e passam a ver o valor manual quando houver; nenhum recalcula nem filtra por origem.

**Guarda:** sem padrão novo (nenhuma regra de cálculo nasceu); allowlists inalteradas.

## 24. IA-06 (2026-10-08) — cardio vira motor dono; prompt e RAG sem número à mão

Ação IA-06 itens (b), (c) e (d) da auditoria de 2026-10-08 (arquivo privado)
(o item (a), `report_ai` interpolando `plateau_min_exposures`, já estava feito).

- **(c) Cardio é motor** (decisão do usuário 2026-10-08). Dono novo `api/app/domain/cardio_policy.py`
  (Python puro, constantes nomeadas): PAI 75, escada de Z2 20/30/40/45 min, recuperação ativa do
  1º ao último degrau, Z2 de correção 20–40 min, Z2 a 60–70% do VO2max, HIIT 5× [2/2 min], memória
  adaptativa com mínimo de 3 ocorrências e o exemplo de evidência (40 min, Aptidão 16, PAI > 80,
  Ramp Rate > 1,5). O `SYSTEM_PROMPT` de `cardio_agent_ai.py` virou f-string e só interpola.
  **Texto renderizado idêntico byte a byte** (snapshot `cardio_agent/*` verde sem regerar;
  `PROMPT_VERSION` ficou em `cardio-agent-v1` naquele PR). Correção (M98 C0, 2026-10-26): o agente
  está hoje em **`cardio-agent-v2`**, desde a IA-10 (texto livre do usuário delimitado por
  `<dados_do_usuario>`, commit `125aee35`); os números continuam interpolados de `cardio_policy`. **M98 C3a (2026-10-26): `cardio-agent-v3`** — saída JSON estruturada (schema strict), validada por `cardio_prescription` e reaplicada pelo envelope de `cardio_envelope` depois do LLM; o prompt interpola também `cardio_prescription` (campos, limites, padrões) e `cardio_envelope` (interferência e tetos); RAG `CARDIO.md` reescrito com placeholders e no índice do agente (§69.4). **M98 C5 (2026-10-27): `periodization-v13`** — a geração de Modelo por IA também interpola os donos do cardio (`cardio_prescription`: campos, limites e padrões; `cardio_envelope`: interferência, faixa do TSB e tetos; `cardio_policy`: preferência e HIIT; `hr_zones`: vocabulário) e injeta o recorte compacto `PERIODIZATION_CARDIO_CHUNK_IDS` de `CARDIO.md` (§69.7).
- **(b) RAG.** `HABILIDADES_TREINO.md` HAB-042 (aquecimento "30% a 40%") e HAB-043 (ajuste "70%"/"80%")
  contradiziam `session_loads` (aquecimento 20%/40%, ajuste 50% + 20 p.p.): agora dizem que a carga
  vem pronta do sistema, no padrão do HAB-004. `COSTAS.md` tinha a mesma contradição em 6 trechos
  ("20% a 50%", "50%"/"70%" no Pulley): reescritos igual. HAB-007/HAB-053 ("8 a 12", "1 a 5", "8–12",
  "1–5") e a panturrilha de `HIPERTROFIA.md` ("10 a 12") remetem ao alvo da prescrição
  (`prescription_defaults`/Modelo). Reps de aquecimento (~20) e de ajuste (6) ficaram: são os campos
  `warmup_reps`/`feeler_set_reps` do item, que a IA preenche, não regra de motor.
  `report_ai`: o exemplo de formatação usa cargas ilustrativas (`X.XXXkg`/`Y.YYYkg`).
- **Versões de prompt:** `workouts-analysis-v2` e `periodization-v9`. O insights (que também injeta
  `HABILIDADES_TREINO.md`) fica em `insights-prompt-v3` neste PR porque `insights_prompt.py` está em
  outro PR em paralelo; a subida vai junto com ele.
- **(d) Guarda:** `exposicoes_minimas`, `fator_carga_trabalho_texto`, `faixa_reps_prosa` e
  `numeros_prompt_cardio`, todas com allowlist vazia.

## 25. Aposentadoria das rotas legadas (COD-05 + M57, 2026-10-09)

**Princípio 5 aplicado** (código morto com regra paralela é apagado). Saíram, depois de 14 dias
com 0 eventos `legacy_endpoint_used` em produção (Logs Insights de 2026-10-08, 93.898 registros
de `musclefit-api-core` e `musclefit-engines`):

- `GET /analytics/muscle-groups`, `/muscle-groups-trend`, `/muscle-groups-table`, com o
  `MUSCLE_GROUP_CASE` (grupo muscular por REGEX no nome do exercício, regra paralela à
  classificação canônica) e o `MUSCLE_GROUP_JOINS`;
- `GET /analytics/dashboard-overview` (sucessora `/analytics/dashboard/overview`), com o card
  `progression_signals_30d` (`VolumeService.get_progression_signal_counts` e
  `exercise_performance.progression_status_counts`, que só ela chamava);
- `GET /analytics/dashboard-engine-audit` (sucessora `/analytics/dashboard/engine`);
- `POST /exercise-mapping-catalog/import` (sucessora `POST`/`PUT /exercise-mapping-catalog`);
- `GET`/`POST /equipment-exercise-mappings` e `DELETE /equipment-exercise-mappings/{link_id}`
  (sucessora `/equipment-exercises`);
- o módulo `legacy_endpoint_telemetry.py`, sem rota marcada.

Nenhum dono mudou e nenhuma regra nova nasceu. **Guarda:** nenhuma allowlist listava essas
cópias (o `MUSCLE_GROUP_CASE` nunca teve padrão); allowlists inalteradas.

## 26. Tonelagem pronta no prompt do resumo de treino (2026-10-10)

No eval pago de 2026-10-10 o `workout_summary` escreveu "16,3 toneladas totais": a entrada
trazia `Volume total: 16285.00 kg` (float cru) e o modelo converteu a unidade por conta própria.

- **Dono:** `load_conventions.py` já era a regra única da tonelagem (M58). Ganhou a exibição:
  `format_tonnage` (kg inteiros, milhar pt-BR, `TONNAGE_DISPLAY_UNIT`/`TONNAGE_DISPLAY_DECIMALS`),
  a mesma forma de `fmtKgTotal` no FE e de `fmt_unit(..., "kg", 0)` no relatório.
- **Consumidor:** `workout_summary_ai.build_prompt` interpola `format_tonnage(total_volume_kg)`
  (`Volume total (tonelagem, soma de repetições × carga): 16.285 kg`) e ganhou uma regra textual,
  sem número: citar só os valores entregues, na mesma unidade, sem converter, arredondar de novo
  nem calcular totais novos. `PROMPT_VERSION` → `workout-summary-v2`.
- **Guarda:** `tonelagem_convertida` ("tonelada" em py/ts/md), nasce vazia.
- **Fora deste PR (semântica, não exibição):** `activities.total_volume_kg` vem de
  `SUM(exercises.volume_kg)` (`services.activity_detail`/lista), que soma todas as séries não
  preparatórias, inclusive `assisted`/`bodyweight`, que `counts_in_tonnage` exclui. Alinhar exige
  mudar o número exibido em `/registro-exercicios` e medir o impacto em produção; fica registrado aqui.

## 27. Replay da progressão preserva a sessão de origem (2026-10-10)

**Não é regra de motor nova**: corrige a gravação do replay. Nenhuma regra de progresso,
regressão, platô ou fase mudou (`comparability.py` e `progression_engine` intocados).

- **Bug:** `progression_evaluator.evaluate_history` juntava as decisões de todas as sessões numa
  lista plana e `progression_state.rebuild_for_user` chamava `persist_result` sem `activity_id`;
  todo replay (`replay_progression.py`, `scripts/rebuild_progression_tracks.py`) regravava
  `progression_decisions.activity_id` como NULL. O `finish` (`run_session_evaluation`) já gravava certo.
- **Assinaturas (dono `progression_evaluator`/`progression_state`):** `EvaluationResult` ganhou
  `session_decisions: tuple[SessionDecisions, ...]` (`SessionDecisions(activity_id, decisions)`),
  preenchido só por `evaluate_history`; `decisions` continua sendo a lista plana, na mesma ordem
  (compatível). `persist_result(..., per_session=True)` grava cada grupo com o `activity_id` da
  sessão de origem; sem o argumento, o comportamento do `finish` não muda.
- **Estado idêntico:** trilhas e grupos gravados são os mesmos (mesmo `fingerprint` de
  `replay_progression`), e as decisões são as mesmas, na mesma ordem; só o vínculo muda de NULL
  para a sessão. Prova: `tests/unit/test_replay_preserva_activity_id.py`; regressão no banco
  real: `test_pr14_replay_operacao.test_replay_grava_cada_decisao_com_a_sessao_que_a_gerou`.
- **Guarda:** nenhuma entrada nova (sem regra numérica); allowlists intocadas.

## 28. Utilitários de data unificados (COD-06, 2026-10-10)

**Não é regra de motor nova** e nenhum comportamento muda: as cópias idênticas viraram uma só.

- **"Hoje do usuário" (dono `time_windows`):** `user_today_for(conn, user_id)` =
  `user_today(user_timezone(conn, user_id))`, a composição que `intervals_integration._user_today`
  e `generation/mode_a._user_today` repetiam. Os nomes locais ficam (alvo de `monkeypatch`).
  `analytics._user_today` (sem `user_id` → fuso default; banco fora → fuso default) e
  `IntelligenceServiceBase._user_today` (fuso memoizado do serviço) têm contrato próprio e ficam.
- **Progressão (dono `domain/progression_engine`):** `progression_evaluator._as_date` era cópia
  idêntica de `progression_engine._as_date` e agora importa do motor.
- **Utilitários puros sem dono de motor** em `app/date_helpers.py`: `to_date` (era
  `coercion.to_date` e `template_assignments._as_date`; `wellness_freshness._as_date` usa com o
  `strip()` que só ela tinha) e `isoformat_or_value` (`_iso` de `progression` e
  `equipment_exercise_preferences`). As variantes que diferem em comportamento ficaram onde estão
  (lista na docstring de `date_helpers.py`); paridade em `tests/unit/test_cod06_date_helpers_paridade.py`.
- **Guarda:** nenhuma entrada nova (sem regra numérica); allowlists intocadas.

## 29. Avaliar contra a ficha congelada e dose da ficha (RPE 10, Lote 2 — P7/P3, 2026-10-11)

Investigação: RPE 10 de 2026-10-11 (arquivo privado) (propostas 7 e 3).

- **P7 — mesma base para prescrever e avaliar.** `training_history.SESSIONS_SQL` lê do
  `prescription_json`, chave a chave (`JSON_EXTRACT`), as séries, o alvo efetivo, o teto, a falha e
  a decisão congelados (`FrozenPrescription`, em `PrescribedSpec.frozen`; snapshot ausente/legado ⇒
  `None`). `progression_evaluator.SessionInputs.evaluation_intensity_for` avalia com o teto/falha da
  ficha + overlay de segurança de hoje (`effective_intensity.from_prescription`, a mesma regra que o
  Modo Treino usa para gravar a série): tela e avaliação concordam sobre a mesma série. O contrato
  de recuperação não é reaplicado (já está na ficha): sessão em dose reduzida que seguiu a ficha
  CONTA. Item com ficha `SKIP`/0 séries treinado ⇒ NEUTRAL `prescription_skip`
  (`progression_engine.REASON_PRESCRIPTION_SKIP`). Sem ficha congelada ⇒ caminho antigo (par
  composto com o contrato informado; READY no `finish`). Replay usa o mesmo caminho.
- **P3 — dose exigida.** `progression_engine.required_dose_sets` = séries da ficha congelada do dia
  (limitadas a `MAX_SETS_COUNT`; fallback `track.sets_count`). `update_track_execution` não sobe
  mais `sets_count` por séries extras; só `ADD_SET` muda.
- **Guarda:** padrões novos `dose_exigida_series` e `ficha_congelada_sql`, ambos com allowlist vazia.
- **Impacto (replay do usuário 1 no DEV, 54 sessões):** NEUTRAL 270 → 221
  (`sets_below_prescribed_dose` 79 → 27; `prescription_skip` 0 → 3), QUALIFY 84 → 113, RESET 61 → 81;
  `sets_count` muda em 43 de 190 trilhas (30 de 4 → 3, 3 de 4 → 2, 10 de 3 → 2; trilhas em 4: 55 → 22);
  carga de trilha: 0 mudanças; grupos 5 e 51 passam de F1 para F2; eventos de segurança re-derivados
  pela avaliação 10 → 58 (agora os mesmos que a tela grava; o Lote 1/P1 torna informativos os de
  teto cortado só pela recuperação); INCREASE_LOAD continua 0 (depende dos Lotes 3 e 4).

## 30. RPE 10 Lote 1 (2026-10-11) — RPE 10 informativo e queda de repetições só na série de trabalho

Investigação RPE 10 de 2026-10-11 (arquivo privado), propostas P1 e P2.

- **P1, semântica do RPE 10 (dono `training_safety` + `effective_intensity` + `progression_engine`):**
  `compose` expõe `failure_denied_by` (`FAILURE_DENIED_BY_PAIR`/`FAILURE_DENIED_BY_RECOVERY`, regra
  `failure_denial_origin`; empate = PAIR). O snapshot congela o campo (aditivo, `schema_version` não muda);
  snapshot antigo deriva de `safety_constraints.max_safe_rpe` (`prescription.failure_denied_by_of`).
  `safety_event_for`: PAIR → `RPE_ABOVE_SAFE_CEILING` (como antes), RECOVERY → `RPE_ABOVE_RECOVERY_TARGET`
  (novo, informativo, cabe no VARCHAR(40), sem migração). `intensity_status_for`/`session_intensity_status`:
  só o par gera `ABOVE_SAFE_CEILING`; negação só pela recuperação segue a classificação normal
  (`FAILURE_NOT_ALLOWED_REACHED_CEILING` quando a série cobrada atinge o alvo: "alvo atingido, falha não
  liberada no dia"). O log `EVENT_SAFETY_RPE_ABOVE_CEILING` só sai para o evento de par (agora com
  `failure_denied_by`). Eventos já gravados e fichas congeladas não são reescritos.
- **Frontend:** `intensity-status.ts` lê códigos e rótulos de `motor-constants.generated.json` (bloco
  `intensity`, exportado de `domain/intensity_status_labels.py`); banner do Modo Treino azul/informativo
  para o evento da recuperação ("RPE acima do alvo do dia (teto N pela recuperação) — a série vale como
  intensidade atingida."), laranja só para o do par.
- **P2, queda de repetições (dono `volume_service` + `muscle_recovery_service`):** só séries `working`;
  sem queda com a última série de trabalho em RPE ≥ `REP_DROP_IGNORE_FROM_RPE` (9); limiar
  `REP_DROP_MIN_FRACTION` (0,30) nomeado; o sinal leva `exercises` (exercício e reps 1ª→última) para o
  banner do Lote 6. Limiares de `classify_readiness` nomeados sem mudar valor.
- **Integração com o Lote 2/P7 (avaliação contra a ficha congelada):** `SESSIONS_SQL` extrai também
  `$.failure_denied_by` e `$.safety_constraints.max_safe_rpe` (`FrozenPrescription.failure_denied_by` /
  `pair_max_safe_rpe`); `SessionInputs.evaluation_intensity_for` devolve a origem que
  `effective_intensity.from_prescription` resolve (congelada ou derivada para snapshot antigo) e
  `evaluate_track_session` a usa nos `safety_events` e no `intensity_status`. Tela e avaliação gravam o
  MESMO evento na mesma série (informativo quando só a recuperação negou a falha). Sem ficha congelada,
  a origem é a de `compose` (par × contrato informado). O padrão `ficha_congelada_sql` passa a cobrir
  `$.failure_denied_by`.
- **Guarda:** padrão novo `queda_reps_entre_series` (allowlist vazia).

## 31. Qualificação pela melhor série, excesso de reps e carga da trilha (RPE 10, Lote 3 — P4/P5, 2026-10-11)

Investigação: RPE 10 de 2026-10-11 (arquivo privado) (propostas 4 e 5).

- **P4 — predicado (decisão do usuário).** A sessão qualifica quando a MELHOR série na carga de topo
  atinge `reps_max` e TODAS ficam ≥ `reps_min` (`progression_engine.evaluate_track_session`; RESET
  novo `REASON_REPS_BELOW_MIN` = `reps_below_reps_min`, checado antes do topo). A D10
  (`regression_triggered`) continua exigindo todas abaixo do piso.
- **P4 — regra de excesso.** `EXCESS_REPS_FAST_TRACK = 3`: QUALIFY cuja melhor série fez
  `reps_max + 3` no RPE alvo sai com `excess_reps`; o avaliador arma o aumento DA TRILHA
  (`arm_excess_reps_fast_track`: `pending_load_kg` = carga da trilha + `load_increment`,
  `pending_load_since`, migração 071) e `prescribe` emite `INCREASE_LOAD` daquela trilha na próxima
  ficha (evidência `excess_reps_fast_track`), sem platô. É ajuste de carga da trilha: não promove a
  fase do grupo nem conta em `group_pending_effective` (o caminho do platô do grupo segue intacto —
  o Lote 4 o torna por trilha). Dose reduzida difere; sai quando a carga alvo é executada ou expira
  em 14 dias; rearmar a mesma carga mantém o instante.
- **P5 — carga da trilha.** `session_working_load(working, reps_min)` = maior carga de trabalho cuja
  série cumpriu o piso (fallback: o mínimo, com a adoção guardada pelo M77). `load_below_track` olha
  só a carga de topo (back-off mais leve não zera). Calibração usa a mesma regra.
- **P5 — efetivação condicionada.** `INCREASE_LOAD` do grupo só é marcado na trilha que executou
  carga ≥ alvo (`execution_applies_pending`, marca estritamente posterior a `pending_payload.since`);
  sem isso em 14 dias, expira sem promover a fase (`group_pending_expired`, evidência
  `pending_action_expired`). `DECREASE_LOAD` segue a regra anterior.
- **Guarda:** padrões novos `qualificacao_melhor_serie` e `excesso_reps_sobe_carga`, allowlists vazias.
- **Instrumento:** `scripts/replay_diff_progressao.py` passa a contar as ações de carga da trilha, as
  expirações do grupo e a PRÓXIMA ficha por trilha (`prescribe` sobre o estado final, READY).
- **Impacto (replay do usuário 1 no DEV contra a `develop` com os Lotes 0/1/2/5, 54 sessões):**
  RESET `reps_below_reps_max` 45 → 8, `reps_below_reps_min` 0 → 22, `load_below_track` 21 → 36
  (efeito da carga de topo), QUALIFY 113 → 114; regra de excesso armou 45 aumentos de trilha
  (8 executados, 9 expirados); decisões de grupo `INCREASE_LOAD` 0 → 1 (grupo 7, pendente),
  `DECREASE_LOAD` 0 → 2 (grupo 52, que chega a F2 mais cedo e entra na D10 em 10-12),
  `SET_REP_RANGE_10_12` 13 → 14; carga de trilha sobe em 46 de 190 trilhas (nenhuma desce);
  próxima ficha `INCREASE_LOAD` em 16 de 190 trilhas (antes 0). Abdominal crunch (exercício 27):
  MTS (equip. 5) passa a 55 kg (13×55 em 01/10 cumpriu o piso; antes 50), máquina (15) segue 50 kg.

## 32. RPE 10 Lote 6 (2026-10-11) — uma voz no Modo Treino (P9)

Investigação RPE 10 de 2026-10-11 (arquivo privado) §4/§6.8: três vocabulários na
mesma tela (decisão do motor, desempenho e categoria da IA), card da série sem status e banner sem o sinal.

- **Status por série (dono `progression_engine`):** `set_intensity_status` usa o mesmo núcleo do status da
  sessão (`_intensity_status_core`), com a série sendo a cobrada e a mais alta; sem alvo efetivo (treino
  livre) só o evento do par se aplica. `effective_intensity.set_intensity_reading` aplica à série `working`
  (preparatória/técnica: sem status). O salvamento da série devolve `intensity_status`,
  `intensity_status_label` e `safety_event_label` (rótulos do dono `intensity_status_labels`). **Sem coluna
  nova:** o status é derivável do que já fica gravado (`rpe`, `rpe_target_effective`, `safety_event`).
- **IA por série (dono `domain/set_evaluation_categories.py`, `PROMPT_VERSION = set-eval-v4`):** categorias =
  subconjunto das ações do motor aplicável a UMA série (`HOLD`, `INCREASE_LOAD`, `DECREASE_LOAD`; `ADD_SET`,
  `APPLY_TECHNIQUE`, `SET_REP_RANGE_10_12` e `SKIP` são decisões de sessão/fase; "aprimorar técnica" não existe
  no motor e vai na nota). O prompt recebe o status da série calculado pelo motor (o mesmo do salvamento:
  `dispatch_evaluate_set` resolve a intensidade do exercício JÁ lançado na sessão em andamento) e a instrução
  de explicar em cima dele, com as mesmas palavras. A categoria final segue a direção da carga final
  (`category_for_load`, depois de `bound_ai_load`); a troca fica em `category_from_ai`. Clamp de RPE e
  `bound_ai_load` mantidos. Categoria desconhecida (resposta antiga em cache) aparece neutra na tela.
- **Frontend:** `set-engine-status.ts` (só cor) mostra no card "Ajuste de hoje: <rótulo do motor>"
  (`prescription.decision`) e o status da série anterior; no descanso, o status da série salva; o card da IA
  usa rótulo/cor por direção do JSON gerado e mostra o corte de carga ("Carga sugerida limitada ao alvo do
  motor: X → Y") separado do corte de RPE. `PROGRESSION_DECISION_LABELS` lê `progression_decision_labels`
  do JSON (paridade em `test_m76_lote1b_espelhos_fe.py`).
- **Banner/briefing dizem o sinal (dono `domain/recovery_signal_summary.py`):** o resumo dos
  `blocking_signals` (código, texto com exercício e reps da queda, data da sessão de origem) é congelado na
  ficha (`prescription_json.recovery_signals`, aditivo) pelos três caminhos de congelamento (Modo A/B via
  evidência de `prescribe`; linhas manuais sem decisão via contrato projetado). Fichas já congeladas não
  mudam (sem o campo, o banner fica como antes).
- **Guarda:** padrão novo `vocabulario_ia_serie` (allowlist vazia).
- **Impacto em produção:** não muda regra de progressão (replay_diff do usuário 1 = 0 diferenças); muda o
  texto do prompt da IA por série (v4) e acrescenta campos aditivos nas respostas e no snapshot.

## 33. Platô por exercício (RPE 10, Lote 4 — P6, 2026-10-11)

Investigação: RPE 10 de 2026-10-11 (arquivo privado) (proposta 6). Decisão do usuário
(reabre a D2): o platô para aumentar a carga é **por exercício**; a fase continua do grupo.

- **Platô da trilha.** `plateau_confirmed` (mesma regra: `PLATEAU_MIN_SESSIONS` = 2 QUALIFY consecutivas em
  ≥ `PLATEAU_MIN_SPAN_DAYS` = 7 dias) roda sobre `progression_evaluator.track_plateau_history`: só as sessões
  DAQUELA trilha desde a entrada do grupo na fase, sem a de calibração (`counts_for_history`), avaliadas com a
  carga já adotada na sessão (depois de um aumento, as sessões na carga antiga zeram por `load_below_track` e
  a sequência recomeça). NEUTRAL/RESET de um exercício não mexe na sequência dos outros.
- **Aumento da trilha, mecanismo único.** Nas fases de carga (`TRACK_LOAD_PHASES` = F2, F6) com trilha de
  carga aplicável (`load_phase_by_track`), o platô confirmado arma o aumento DA TRILHA
  (`arm_track_plateau_load` → `arm_track_load_increase`, o mesmo do excesso do Lote 3: `pending_load_kg` =
  um passo, `pending_load_since`; origem em `pending_load_source` = `track_plateau`, migração 072). Na mesma
  carga alvo o platô vence o excesso como origem e o instante é mantido. Efetivação e expiração do Lote 3/P5
  (carga executada ≥ alvo; 14 dias). Efetivado por platô grava `plateau_load_applied_at`. Na F6, o intervalo
  `F6_LOAD_INTERVAL_DAYS` passa a contar da última mudança de carga da trilha. `prescribe` emite
  `INCREASE_LOAD` com a evidência `track_plateau_load_increase` e o motivo interpolado das constantes.
- **Fase do grupo.** F2 → F3 quando a maioria ESTRITA (`GROUP_PHASE_ADVANCE_TRACK_SHARE` = 0,5: 1 de 1, 2 de 2,
  2 de 3, 3 de 4) das trilhas ativas com carga do grupo na janela de 21 sessões (`group_window_tracks`)
  efetivou o aumento por platô desde a entrada na fase (`group_load_phase_complete`, `advance_load_phase`,
  evidência `group_phase_advance_by_tracks`). O aumento pelo excesso não conta (o Lote 3 decidiu que ele não
  promove a fase). Trilhas por contexto de fadiga contam separadas (cada contexto é uma trilha).
- **Ações de fase sem carga** (F1 `SET_REP_RANGE_10_12`, F3 `APPLY_TECHNIQUE`, F4/F5 `ADD_SET`): seguem pelo
  platô do GRUPO (`evaluate_group_session` + `group_session_history`). Avaliado e mantido no grupo: são
  decisões de PROGRAMA do grupo (a faixa vale para todas as trilhas, a técnica e a série escolhem UMA trilha
  alvo do grupo); torná-las por trilha mudaria a semântica de fase e fica para decisão do usuário.
- **Legado e efeitos colaterais.** O grupo não arma mais `INCREASE_LOAD`; uma ação gravada antes do Lote 4 é
  efetiva só pela maioria (`group_pending_effective`) e, senão, sai da fila sem promover a fase (evidência
  `group_increase_load_moved_to_tracks`; `prescribe` ainda a lê até a próxima avaliação). `group_pending_expired`
  e a evidência `pending_action_expired` saíram. A D10 (`regression_triggered`) não muda, mas a efetivação de
  um `DECREASE_LOAD` numa fase de carga não reinicia mais `phase_entered_at` (zeraria o platô e os aumentos
  das outras trilhas). Sem ação de grupo, a decisão de avaliação do grupo registra `INCREASE_LOAD` quando
  uma trilha armou o aumento por platô na sessão. A F6 só de peso corporal não arma mais aumento de grupo.
- **Guarda:** padrão novo `plato_por_trilha` (allowlist vazia).
- **Deploy:** migração 072 antes do código (o job de deploy já aplica com `RUN_DB_MIGRATIONS=true`); depois da
  promoção, a ação `replay-progression` recompõe trilhas e grupos.
- **Impacto (replay do usuário 1 no DEV contra a `develop` c10cab0, 54 sessões):** vereditos das trilhas
  idênticos (QUALIFY 114, RESET 80, NEUTRAL 221); aumentos por platô da trilha armados 4 (Crucifixo na máquina
  Pec Deck pré-fatigado em 05/09, Stiff no Smith em 28/09, Remada baixa no cabo pré-fatigada e Remada curvada
  no Orbital Row em 09/10), 1 efetivado (Crucifixo, 13/09); decisões de grupo `INCREASE_LOAD` 1 → 4 (o 1
  antigo era o do grupo 7 Costas meio, que deixa de existir); nenhum grupo chega a F3 (Peitoral inferior fica
  com 1 de 8 trilhas efetivadas); cargas finais das trilhas idênticas; próxima ficha `INCREASE_LOAD` 16 → 10
  (somem 7 trilhas do grupo 7 Costas meio que subiam pelo `INCREASE_LOAD` do GRUPO sem 2 qualificantes
  próprias; entra Stiff no Smith 40 → 45 kg; Remada curvada no Orbital Row 25 → 30 kg continua, agora pelo
  platô dela). Abdominal crunch e
  Supino: sem mudança (nenhuma trilha deles tem 2 QUALIFY próprias em ≥ 7 dias na mesma carga).

## 34. Carga de topo repetida e fase por exercício (RPE 10, Lote 7 — D1/D2, 2026-10-11)

Decisões do usuário de 2026-10-11, ajustes de produto depois dos Lotes 0–6. Dono: `domain/progression_engine.py`.

- **D1 — a carga da sessão exige a carga de topo repetida.** `session_working_load(working, reps_min)` passa a
  devolver a MAIOR carga L tal que (a) as séries de trabalho com carga ≥ L são pelo menos
  `top_load_min_sets(n)` = `ceil(n × TOP_LOAD_MIN_SHARE_OF_WORKING_SETS)` (= 0,5; 3 séries: 2; 4 séries: 2; 1 série:
  1) das `n` séries de trabalho com carga e (b) ao menos uma série com carga ≥ L cumpriu `reps_min`. As duas
  condições são monótonas, então L = mín(maior carga que cumpriu o piso, k-ésima maior carga). Sem nenhuma série
  no piso (ou `reps_min` desconhecido), o fallback de sempre: o mínimo, com a adoção guardada pelo M77
  (`below_floor_load_is_working`). Exemplos (piso 10): 16×50, 13×55, 9×55, 6×50 → 55; 12×35, 12×35, 12×45 → 35;
  10×40, 10×40, 8×45, 8×45 → 40. Antes (Lote 3/P5) uma única série pesada no piso virava a carga da trilha e as
  sessões seguintes na carga habitual davam RESET `load_below_track`.
- **Uma regra, todos os consumidores do motor.** A mesma função decide a calibração, a adoção
  (`adopted_working_load`), o RESET `load_below_track` (`evaluate_track_session`: só a carga da sessão conta,
  back-off mais leve não zera; agora uma série isolada acima também não sobe a régua), a melhor série na carga de
  topo (`top_load_sets`) e a efetivação dos aumentos (`execution_applies_pending`, `track_pending_load_status`:
  carga executada ≥ alvo — uma série isolada na carga alvo não efetiva o aumento).
- **D2 — a fase do grupo conta por exercício.** `group_load_phase_progress` conta EXERCÍCIOS
  (`TrackState.exercise_id`): denominador = exercícios distintos com trilha ativa de carga no grupo na janela
  (`group_window_tracks`); numerador = exercícios em que pelo menos uma trilha (qualquer equipamento/contexto de
  fadiga) efetivou o aumento por platô desde a entrada na fase. Maioria estrita mantida
  (`GROUP_PHASE_ADVANCE_TRACK_SHARE` = 0,5); o aumento pela regra de excesso continua NÃO contando. A evidência
  `group_phase_advance_by_tracks` ganha `unit: "exercise"`. O `INCREASE_LOAD` legado do grupo
  (`group_pending_effective`) usa a mesma contagem.
- **Sem migração.** Nenhuma coluna nova; o replay (`replay-progression`) recompõe trilhas e grupos com a regra nova.
- **Guarda:** padrão novo `carga_topo_repetida` (allowlist vazia); `plato_por_trilha` segue cobrindo a maioria.
- **Impacto (replay do usuário 1 no DEV contra a `develop` 2a9c226, 54 sessões):** vereditos QUALIFY 114 → 115,
  RESET 80 → 79 (`reps_below_reps_max` 8 → 7). RESET `load_below_track` **36 → 36**: entram 2 (16:26 pré-fatigada
  em 16/08 — 6×38,3 isolada; 94:26 em 01/09 — 12×11,3 isolada) e saem 2 (94:26 em 03/10 e 119:85 em 05/10, que
  antes ficavam abaixo de uma carga adotada de uma série só). Das 36 restantes, 33 têm TODAS as séries abaixo da
  carga da trilha (a pessoa treinou mais leve que a carga que já tinha repetido) — não são série isolada.
  Decisões por tipo idênticas (INCREASE_LOAD 4, DECREASE_LOAD 2, SET_REP_RANGE_10_12 14). Ações de carga da
  trilha: armadas 45 → 46, efetivadas 7 → 6, expiradas 9 → 7; platô da trilha armado 4 / efetivado 1 (iguais).
  Cargas finais: 14 trilhas mudam, todas para baixo (a carga deixa de vir de uma série de topo isolada; ex.
  22:50 100 → 60, 8:81 54 → 45, 29:29 82 → 75, 102:50 97 → 87, 109:26 40 → 33); **Abdominal crunch 27:5 continua
  em 55 kg** (01/10: 2 de 4 séries em 55). Fases: **nenhum grupo chega a F3**; com a contagem por exercício o
  grupo 52 fica com 1 de 4 exercícios (antes 1 de 8 trilhas) e os demais com 0 de N (ex. grupo 10: 0 de 5
  exercícios, eram 14 trilhas). Próxima ficha `INCREASE_LOAD` 10 → 13 (entram 21:20 75 → 80, 94:26 10 → 12,5 e
  119:85 10 → 12,5: aumentos por excesso armados agora que a carga da trilha é a repetida).

## 35. IA por série v5 — status de esforço, reps contra a faixa e dor muscular alta (2026-10-12)

Eval pago v3 × v4 (8 casos): no caso `dor_muscular_alta_com_nota` (Rosca inversa, 3 reps numa faixa 12-15,
dor muscular 9/10, RPE 10) a v4 respondeu `HOLD` 15 kg com "Alvo atingido… RPE 10 aceitável"; a v3 reduzia.
Causas: o status da série avalia só o esforço, mas o rótulo "Alvo atingido" era lido como alvo geral; o prompt
não tinha regra para dor muscular alta; e a IA copiava a linha de status literalmente.

- **Rótulo de esforço (dono `domain/intensity_status_labels.py`):** `REACHED` → "Esforço atingido",
  `BELOW_TARGET` → "Esforço abaixo do alvo", `FAILURE_NOT_ALLOWED_REACHED_CEILING` → "Esforço atingido (falha não
  permitida)"; `ABOVE_SAFE_CEILING` mantém "RPE acima do teto seguro". Mudança **global** (série no Modo Treino e
  sessão no dashboard /progressao), porque o status é o mesmo (`_intensity_status_core`) e é de esforço nos dois
  lugares — rótulos separados para a série criariam dois nomes para o mesmo código.
- **Reps contra a faixa (dono `progression_engine.set_reps_position`):** abaixo da faixa / dentro / no teto /
  acima, só em série `working`, com a faixa da ficha congelada (salvamento da série) ou da decisão do motor
  (avaliação; sem decisão, a faixa exibida). Sem faixa: omitido. Vai na resposta do salvamento
  (`reps_position`, `reps_position_label`, aditivo, sem coluna nova) — o card do Modo Treino mostra um selo
  neutro ao lado do status — e no prompt, ao lado do status de esforço.
- **Dor muscular alta (dono `muscle_recovery_service.muscle_pain_blocks_direct_work`):** o mesmo predicado do
  `classify_readiness` (> `MUSCLE_PAIN_BLOCK_ABOVE`). Com dor acima dele o prompt manda `DECREASE_LOAD` (carga
  abaixo da executada, no passo do equipamento) ou encerrar o exercício, e diz que o status de esforço não
  justifica manter; a regra crítica do RPE 10 passa a exceptuar a dor muscular alta. `category_for_load` e
  `bound_ai_load` inalterados.
- **Prompt menos robótico:** a IA explica com palavras próprias, usando o rótulo do motor, sem copiar a linha de
  status. Critério novo no eval (`COPIA_DA_LINHA_DE_STATUS`) e o caso de dor passa a exigir `DECREASE_LOAD`.
- **Nota, categoria e carga concordam:** o prompt recebe a regra de `category_for_load` em texto, gerada pelo
  dono (`set_evaluation_categories.category_rule_text`: categoria = direção da carga sugerida × executada; a nota
  diz a mesma direção com o rótulo). Critério novo no eval: categoria trocada pelo servidor (`category_from_ai`)
  = nota escrita para outra direção. Achado do 1º eval da v5: `HOLD` "repita a carga" com 25 kg sugeridos e 20
  executados (a categoria final virava "Aumentar carga").
- **Guarda:** padrões novos `limiar_dor_muscular` e `rotulo_status_serie` (allowlists vazias).
- **Impacto:** não muda progressão (replay sem diferença); muda o texto do selo no card/dashboard e o texto da IA.

## 36. Carga da próxima série dentro da sessão — regra do usuário (set-eval-v6, 2026-10-12)

Regra do usuário (literal): "se para atingir o RPE 10 a série precisou passar de 15 repetições ou se parou em 12
repetições com RPE < 9 então a carga precisa ser aumentada. Se o RPE 10 veio antes de 6 repetições então a carga
deve ser reduzida."

- **Dono novo `domain/next_set_load.py`** (Python puro, constantes nomeadas): `NEXT_SET_INCREASE_ABOVE_REPS` = 15,
  `NEXT_SET_INCREASE_MIN_REPS_LOW_RPE` = 12, `NEXT_SET_LOW_RPE_BELOW` = 9, `NEXT_SET_DECREASE_BELOW_REPS` = 6 e
  `NEXT_SET_FAILURE_RPE` = `training_safety.FAILURE_RPE`. `suggest_next_set_load` devolve direção (código de ação do
  motor: `INCREASE_LOAD`/`HOLD`/`DECREASE_LOAD`), carga e motivo (código + rótulo com os limiares interpolados).
- **Premissas do TL (aplicadas):** aumentar quando reps > 15 com qualquer RPE (16+ reps = carga leve; vale também sem
  RPE) ou reps ≥ 12 com RPE informado < 9; reduzir quando RPE ≥ 10 com reps < 6; senão manter. Prevalência: dor
  articular ou dor muscular acima de `MUSCLE_PAIN_BLOCK_ABOVE` (`muscle_pain_blocks_direct_work`, o mesmo predicado da
  v5) reduz acima de tudo. Sem RPE só a condição reps > 15 dispara.
- **Passo:** o do equipamento (`load_increment`, D12), carga = executada ± passo arredondada ao múltiplo do passo
  (`round_to_equipment_step`, meio para cima; 21,3 kg com passo 2,5 → 25 / 20). Manter devolve a carga executada
  exata. Reduzir abaixo do primeiro passo: direção sem carga.
- **Escopo:** só série `working` com carga > 0 e reps. Fora: aquecimento/ajuste/aplicação de técnica e as convenções
  `assisted` e `bodyweight` (`load_conventions`, lida por par em `load_conventions.pair_load_convention`). `assisted`
  NÃO é invertido de propósito: o rótulo "Aumentar carga" diria mais assistência (o contrário do que se quer); nenhuma
  série `assisted` existe no histórico do usuário 1. `bodyweight_plus` (lastro) vale.
- **Intra-sessão:** a prescrição da próxima SESSÃO continua do motor de progressão (qualificação, excesso +3, platô);
  replay do usuário 1 com 0 diferenças.
- **IA por série (set-eval-v6):** `bound_ai_load` passa a devolver a carga da regra (a IA não sobe até o alvo do motor
  quando a regra manda manter, nem sobe/desce contra ela; ajuste `NEXT_SET_LOAD_RULE` em `load_adjustments[]`); a
  categoria é a direção da regra. Fora da regra, o comportamento anterior (passo + teto do alvo do motor). O prompt
  recebe a sugestão pronta e a regra com os limiares interpolados; a regra crítica de RPE deixa de dizer que RPE 10
  nunca reduz (RPE 10 antes de 6 reps reduz).
- **Sem IA:** o salvamento da série devolve `next_set_load` (direção, rótulo, carga, motivo e rótulo); o Modo Treino
  mostra "Próxima série: <rótulo> → X kg" com o motivo (rótulos do JSON gerado) e pré-preenche a carga da próxima série
  com ela (antes: a carga da última série de trabalho). Com IA, a sugestão da IA (que já é a da regra) continua na
  frente.
- **Guarda:** padrão novo `carga_proxima_serie` (allowlist vazia).
- **Impacto medido (DEV, usuário 1, 1.770 séries `working` com carga):** a regra diria AUMENTAR em 719, MANTER em
  1.042, REDUZIR em 9 (6 séries `bodyweight` fora). Nos últimos 30 dias (414 séries): MANTER 376, AUMENTAR 34,
  REDUZIR 4. Dos AUMENTAR com série seguinte, a pessoa manteve a carga em 494 e subiu em 121 (últimos 30 dias: subiu
  18, manteve 12); dos MANTER, manteve em 535 de 585. 499 dos 507 "12+ reps com RPE < 9" são de séries antigas sem
  alvo de RPE.

### 36.1 Rampa planejada de RPE como exceção (set-eval-v7, 2026-10-12)

Decisão do usuário: "Trate a rampa, desde que planejada como exceção".

- **Regra (dono `next_set_load`)**: com rampa de RPE PLANEJADA na ficha (dois ou mais degraus diferentes, ex.
  `"8-9-10"`), o limiar da condição "12+ reps com RPE baixo" passa a ser `min(NEXT_SET_LOW_RPE_BELOW, RPE planejado
  da série)`. "8-9-10": série 1 (planejada 8) 12@8 → MANTER, 12@7 → AUMENTAR; série 2 (planejada 9) 12@8 → AUMENTAR.
  A condição de mais de 15 reps, a de redução (RPE 10 com menos de 6 reps) e a prevalência da dor não mudam. Alvo
  único (`"9"`, `"9-9"`) não é rampa. Quando a rampa evita o aumento, o motivo é `NEXT_SET_WITHIN_PLANNED_RAMP`, com o
  rótulo "RPE dentro da rampa planejada (alvo 8 nesta série)" (`NextSetLoad.reason_label`; o FE prefere o rótulo do
  servidor e cai no do JSON gerado pelo código).
- **Parser**: nenhum novo — `training_history.parse_rpe_ramp` + `PrescribedSpec.rpe_target_for_set` (rampa mais curta
  repete o último degrau), em `next_set_load.planned_ramp_rpe(rpe_target, índice da série de trabalho)`.
- **De onde vem a rampa**: no salvamento da série, `effective_intensity.plan_rpe_target` (`prescription_json.rpe_target`
  da ficha congelada, senão `workout_plan_exercises.rpe_target`) e o índice = séries `working` já gravadas no exercício
  (`COUNT` com `set_types.sql_is_working`); na IA por série, o `rpe_target` do payload (o que o Modo Treino mostrou) e
  o índice = séries `working` em `previous_sets_this_session` (`engine_decision_context.payload_working_set_index`).
  Treino livre (sem ficha): sem rampa.
- **Prompt `set-eval-v7`**: com rampa, a seção da próxima série ganha a linha "Rampa de RPE planejada na ficha: RPE N
  nesta série…" (números interpolados). Eval pago 10/10 (nenhum caso tem a rampa evitando o aumento; o de dor tem a
  linha da rampa).
- **Impacto medido (DEV, usuário 1)**: 600 das 1.770 séries `working` com carga têm rampa planejada; 85 AUMENTAR
  viram MANTER pela rampa (719 → 634); nos últimos 30 dias, 0. Replay da progressão: 0 diferenças.

## 37. Espelhos do FE gerados pelo dono: Intensidade × Densidade (E13) e contabilização de técnica (2026-10-13)

Sem mudança de semântica: os números e as fórmulas são os mesmos; o que muda é QUEM é o dono e de
onde o FE lê.

- **Intensidade × Densidade (E13).** O dono era o próprio `frontend/src/lib/intensity-density.ts`
  ("sem dono no backend"). Dono novo `domain/intensity_density.py` (Python puro, constantes nomeadas:
  `RPE_INTENSITY_POINTS`, `FAILURE_LAST_SET_RPE`, `REPS_DENSITY_MIN/MAX`, `PEAK_DENSITY_MIN/MAX_SECONDS`,
  `REST_DENSITY_LONGEST/SHORTEST_SECONDS`, `SETS_VOLUME_MIN/MAX`, `BALANCED_BAND`, `POSITION_MIDPOINT`,
  `INTENSITY_DENSITY_ROLES`), com `compute_item`, `direction_of` e `aggregate_by_group`. Sem parser
  novo: rampa por `training_history.parse_rpe_ramp`, reps por `time_estimate.parse_reps_target`, falha
  por `training_safety.FAILURE_REQUIREMENTS_NEEDING_FAILURE`. O TS continua calculando na tela (o corpo
  do preview não leva RPE, descanso nem pico, e o builder recalcula a cada tecla), mas lê a seção
  `intensity_density` de `motor-constants.generated.json` e roda contra `intensity-density.parity.json`
  (rampas, 97 itens e 5 cenários de grupo calculados pelo dono). A agregação por grupo virou
  `aggregateIntensityDensityByGroup` (o casamento com o preview continua em
  `buildIntensityDensityByGroup`). Único ajuste de borda: `sets_count` entre 0 e 0,5 (arredonda a 0
  séries; o campo do builder é inteiro) dava `NaN` no TS e agora dá "sem intensidade" nos dois lados.
- **Contabilização de técnica.** `technique-accounting.ts` copiava à mão `REPS_WEIGHTED_CODES`,
  `BEYOND_FAILURE_WEIGHTS`, `SETS_ONLY_CODES`, `DEFAULT_MAX_EQUIVALENT`, `FULL_EFFORT_RPE`,
  `DEFAULT_REFERENCE_RPE` e a tabela de pontos de esforço (com o clamp 6,5..10). Agora lê a seção
  `technique_accounting` do JSON gerado por `technique_accounting.export_for_frontend()`; a tabela de
  esforço sai do dono real, `MuscleRecoveryService._EFFORT_POINTS` (o mesmo que `effort_factor` já
  usava), sem terceira cópia, e o clamp do TS vem do primeiro e do último ponto. Os coeficientes POR
  TÉCNICA continuam vindo do catálogo pela API. A paridade deixou de ser casos duplicados à mão
  (`technique-accounting.test.ts` × `test_technique_accounting.py`): o vitest itera
  `technique-accounting.parity.json` (`effort_factor`, `application_equivalent`,
  `realized_block_equivalent` com total/por técnica/cru/teto, `planned_equivalent`, `reference_reps`).
- **Regenerar:** `cd api && python tests/unit/test_m76_lote1b_espelhos_fe.py --gravar`; o teste
  Python falha se o JSON commitado divergir do dono, e `test_*_nao_mudou_de_semantica` fixa os números
  que os testes antigos (escritos a partir do TS) davam.
- **Guarda:** padrões novos `intensidade_densidade`, `pesos_tecnica` e `pontos_esforco`, todos com
  allowlist vazia (as cópias do TS saíram neste PR).
- **Ajustes da revisão (mesmo PR, JSON gerado inalterado):** `FAILURE_LAST_SET_RPE` deixou de ser `10.0`
  escrito à mão e passou a ser `float(training_safety.FAILURE_RPE)`; o clamp de `effort_points` usa o
  primeiro e o último ponto de `_EFFORT_POINTS` (não mais `6.5, 10.0`) e o export do FE lê o alias
  público `MuscleRecoveryService.EFFORT_POINTS`; o desempate por nome em `aggregate_by_group` deixou o
  codepoint e passou a `_name_collation_key` (letras sem acento/caixa, depois acentos na ordem do ICU,
  depois minúscula antes de maiúscula), igual ao `localeCompare('pt-BR')` do TS, que não mudou; cenário
  novo de empate na fixture ("Abdomen" < "Abdômen" < "Abdutores Quadril" < "core" < "Core", e
  "Pá" < "Pà" < "Pâ" < "Pä" < "Pã").

## 38. Modo de contagem do volume por Modelo — Lote 1: motor + loader (2026-10-16)

Reabre o M45 (descartado em 05/10 pela EXG-014). O risco de contar o indireto em dobro contra as
faixas do primário fica neutralizado pelas faixas próprias por modo (Lote 2).

**Dono — `intelligence/methodology_volume.py`**
- `VolumeCountingMode` (`primary_only` = default e comportamento de v1..v4; `primary_secondary`).
- `COUNTED_ROLES_BY_MODE`: PRIMARY em `primary_only`; PRIMARY + SECONDARY em `primary_secondary`.
  Estabilizador nunca.
- `counted_contributions(contribuição, modo)`: peso que o modo usa. Em `primary_only` toda PRIMARY vale
  `PRIMARY_ONLY_WEIGHT` (1), inclusive a PRIMARY cadastrada com 0,80. Em `primary_secondary` vale o
  `stimulus_weight` cadastrado, primário inclusive. Peso ≤ 0 não conta.
- Contrato do motor: `mapping_id -> linhas cruas (ContributionRow)` ou `{grupo: GroupContribution(weight, is_primary)}` já resolvido. O conjunto de grupos
  da v4 continua aceito como atalho de `primary_only`.
- **Regra do usuário para `primary_secondary`:**
  1. Séries: cada item soma `sets × stimulus_weight` (técnica também ponderada) no grupo. As frações se
     somam entre exercícios e contam como estão, sem virar série inteira (`_num` só corta a 2 casas,
     como na v4).
  2. Frequência (bucket 1x/2x/3x+): a sessão conta se o grupo tem trabalho PRIMARY nela **ou** se as
     frações de SECONDARY do grupo naquela sessão somam ao menos `SECONDARY_SESSION_MIN_SETS` (1). As
     frações de sessões diferentes somam séries na semana, mas não viram sessão.
  3. `direct_exercises`: só PRIMARY, nos dois modos.
- Faixas: a EXG-014 vale para `primary_only`. `primary_secondary` é opção do usuário, com faixas
  próprias a partir do Lote 2. Até lá, os dois modos usam as faixas atuais. O motor continua
  recebendo as `VolumeClassSpec` já do modo pedido; quem escolhe as faixas é o loader (ponto de
  extensão em `load_methodology_inputs`).

**Loader — `methodology_volume_service.load_volume_contributions(cur, ids, modo)`.** Substitui
`load_primary_contributions`. Lê `role` + `stimulus_weight` (`is_active = 1`, só os papéis do modo, um
statement) e entrega as linhas CRUAS (`ContributionRow`), sem juntar nada: papel contado, peso por modo
e "mesmo grupo em dois papéis" (maior peso, primário se algum papel for) são decididos só no motor
(`counted_contributions`). `load_methodology_inputs` mantém a herança do canônico para id absorvido.

**Consumidores.** Neste lote todos seguem em `primary_only`: editor/diagnóstico/avisos
(`evaluate_slots`/`evaluate_template`), validador da IA (`periodization/validation.py`), complemento de
volume (`volume_complement.py`, `blocks_of` pelos grupos de `counted_contributions`), teto de ativação
(`ActivationCeiling` + `progression_state.load_activation_ceiling`) e o rótulo de grupo do relatório
Markdown (`primary_only` sempre: rótulo não é volume).

**Métrica.** Continua `methodology-volume-v4`. O payload não mudou: nenhum campo novo, e em
`primary_only` a saída é idêntica bit a bit. A v5 subiu no Lote 2 (§39), que expõe o modo no payload.

**Recuperação fica fora (§3.2).** A recuperação usa o `fatigue_weight` de todos os papéis, estabilizador
inclusive, pelo `VolumeService`, e não lê o modo. Isso é fixado por
`test_recuperacao_fatigue_weight_todos_os_papeis.py`.

**Guarda.** Padrão novo `modo_contagem_volume`, com allowlist vazia. `peso_secundario` continua vazia:
o peso vem do cadastro, nunca escrito à mão.

**Medição (Lote 0, `tools/medir_modo_contagem_volume.py`, só leitura).** Rodada no DEV com os dados de
produção do usuário 1, nos 2 Modelos ativos dele (28 "ABC-DEF" e 21 "ABCDEF Avançado"). É o pior caso,
porque `primary_secondary` foi comparado com as faixas de `primary_only`:
- 29 grupos com volume, 14 com transição de status ou bucket;
- Modelo 28: Bíceps 12 → 21,5, Glúteos 12 → 23,95, Tríceps 12 → 20 e Ombros 12 → 16,15 passam de
  WITHIN a ABOVE; Antebraço 6 → 12,3 passa de BELOW a WITHIN;
- Modelo 21: Glúteos 4,8 → 17,13 passa de BELOW a ABOVE; Tríceps, Peitoral e Antebraço passam de BELOW
  a WITHIN.

Os `stimulus_weight` SECONDARY dos itens desses Modelos vão de 0,15 a 0,80. Somando os dois Modelos,
só 21 de 57 contribuições são 0,50.

## 39. Modo de contagem do volume por Modelo — Lote 2: schema, versão do Modelo e faixas por modo (2026-10-16)

**Migração 073 (`api/db/migrations/073_modo_contagem_volume.py`).** Aditiva, idempotente, reversível
e sem FK para `exercise_catalog`:
- `workout_plan_template_versions.volume_counting_mode` e o espelho em `workout_plan_templates`:
  ENUM(`primary_only`, `primary_secondary`) NOT NULL DEFAULT `primary_only`. É espelho literal de
  `VolumeCountingMode`, com paridade em `test_migration_073_plano.py`. O código antigo grava o
  default, que é o comportamento dele.
- **As tabelas da 057 não mudam** (`muscle_volume_class_ranges` e `muscle_volume_classes.weekly_*`):
  nenhuma coluna nova, a PK fica igual. Elas **são** o cadastro de `primary_only` por definição.
- Os modos ≠ `primary_only` ganham duas filhas com FK CASCADE para `muscle_volume_classes`:
  - `muscle_volume_class_mode_ranges (class_id, counting_mode, frequency_bucket, …)`, com a forma da 057
    mais `copied_from_counting_mode`;
  - `muscle_volume_class_weekly_targets (class_id, counting_mode, …)`.
- Por que filhas, e não o modo na PK da 057: o código antigo lê `muscle_volume_class_ranges` sem
  filtro e o `PUT /ranges` antigo apaga por `class_id`. Na janela de deploy ou num rollback do código,
  isso misturaria os modos e apagaria o ponderado. Com as filhas, o código antigo nunca vê o modo
  ponderado.
- "Não calibrada" é a coluna `copied_from_counting_mode`, não um valor novo de `*_source`.
  `*_source` continua dizendo a procedência do número, e a cópia mantém a do original.
- Seed: o modo ponderado nasce como cópia exata de cada faixa e meta de `primary_only`, com
  procedência e evidência, marcada `copied_from_counting_mode = 'primary_only'`. São dois `INSERT …
  SELECT … NOT EXISTS`, reexecutáveis enquanto a forma final não fecha (mesma regra do backfill da 065).
- `plan_down` recusa reverter enquanto algum Modelo ou versão usar `primary_secondary`.

**Versão do Modelo (`template_versions.py`).** O modo está em `_VERSION_COLUMNS`,
`INSERT_VERSION_SQL`, `REVERT_INSERT_SQL`, `canonical_header` e `TemplateVersion`/`row_to_version`.
O default vem de `counting_mode_of`, que importa de forma tardia
`methodology_volume.DEFAULT_VOLUME_COUNTING_MODE`; não há literal próprio. Trocar o modo é mudança
estrutural e abre versão nova com `effective_from`.

O router (`workout_plan_templates.py`) grava o modo no POST, no PUT, ao duplicar e ao reverter. GET,
lista e detalhe de versão devolvem o modo. `WorkoutPlanTemplateIn.volume_counting_mode` é validado
pelo enum do motor (`models.counting_mode_value`). `None` segue a semântica de `template_type`: a
criação usa o default e a edição **preserva** o modo gravado. O rascunho (067) leva o campo no
payload opaco.

**Motor/loader.** `load_volume_classes(cur, modo)` é o único lugar que escolhe a fonte pelo modo:
- em `primary_only`, as mesmas duas leituras de antes da 073;
- nos outros modos, as filhas filtradas pelo modo. Um bucket sem linha dá `NO_RANGE_FOR_FREQUENCY`,
  nunca a faixa de outro modo.

`evaluate_template` usa o modo **persistido** no Modelo (`counting_mode_of_template`). No preview, o
modo vem do corpo; sem ele, da versão vigente de `template_id`; senão, `primary_only`. O payload ganha
`counting_mode` e a métrica sobe para **`methodology-volume-v5`**. Em `primary_only` os números são os
da v4.

**Continuam em `primary_only` neste lote:**
- validador e complemento da IA, entregues no Lote 5 (§42);
- progressão, teto de ativação e realizado de 7 dias, entregues no Lote 4 (§41);
- relatório Markdown (rótulo, `primary_only` sempre: rótulo não é volume).

As telas e a comparação entre modos foram entregues no Lote 3 (§40).

**CRUD admin (`muscle_volume_classes.py`).** `GET`, `PUT /{id}` (meta semanal) e `PUT /{id}/ranges`
aceitam `?counting_mode=`. Com `primary_only` (o default), escrevem e leem as tabelas da 057
exatamente como antes. Com os outros modos, usam só as filhas. A resposta traz `uncalibrated` e
`weekly_uncalibrated`. Gravar séries ou meta diferentes da cópia limpa a marca. Um macro-grupo criado
depois da 073 nasce sem faixas e sem meta no modo ponderado; a tela do Lote 3 decide como oferecê-las.

**Guarda.** Padrão `faixas_sem_modo`, com allowlist vazia: SQL nas filhas sem `counting_mode =` na
mesma linha. As tabelas da 057 ficam fora porque são de `primary_only` por definição.
`modo_contagem_volume` e `peso_secundario` continuam vazias.

**Impacto em produção.**
- Com o default `primary_only` em todo Modelo e versão, a avaliação, os avisos, a progressão e o
  realizado ficam idênticos. A métrica muda só no rótulo (v5) e no campo `counting_mode`.
- **O deploy exige `RUN_DB_MIGRATIONS=true`**: o código novo lê `volume_counting_mode` e as filhas.
- **Rollback do código:** antes de reverter, voltar para `primary_only` os Modelos em
  `primary_secondary`. O código antigo grava o espelho Modelo/versão sem o modo, e os dois divergem.
  As tabelas da 057 nunca são tocadas pelo modo ponderado, então um rollback não mexe em nenhuma
  faixa nem meta de `primary_only`.
- **Checklist da promoção:**
  1. dry-run da 073 contra produção pelo workflow de migrations, com os 6 statements esperados
     (2 CREATE, 2 seed, 2 ADD COLUMN);
  2. smoke: `GET /muscle-volume-classes` igual ao de antes;
  3. smoke: avaliação de um Modelo com os mesmos números da v4.
- Leitura de produção em 16/10 (só SELECT): 9 linhas em `muscle_volume_class_ranges` e 3 em
  `muscle_volume_classes`, com metas já editadas pelo usuário (Grandes 15–20, Pequenos 12–14, Outros
  6–12). Nenhuma coluna de modo existe ainda. Portanto o seed cria 9 faixas e 3 metas ponderadas, e
  nenhum Modelo pode estar em `primary_secondary`.
- Ao escolher o modo ponderado, as faixas são a cópia das atuais. O pior caso medido no Lote 0 (§38)
  vale até o admin recalibrar.

## 40. Modo de contagem do volume por Modelo — Lote 3: telas, comparação e fatia por item (2026-10-16)

**Dono continua `intelligence/methodology_volume.py`.** Este lote não cria regra nova de contagem; ele expõe
o que o motor já calcula, para as telas só exibirem.
- `VOLUME_COUNTING_MODE_LABELS`, `COUNTED_SETS_UNIT_LABELS`/`COUNTED_SETS_UNIT_SINGULAR_LABELS` ("séries
  diretas" × "séries ponderadas") e `counting_mode_description(modo)` (régua do modo, com
  `SECONDARY_SESSION_MIN_SETS` interpolado). `export_for_frontend()` vira a seção `methodology_volume` de
  `motor-constants.generated.json` (gerado por `test_m76_lote1b_espelhos_fe.py --gravar`); o FE lê por
  `frontend/src/lib/volume-counting-mode.ts`. A seção exporta também `frequency_buckets` (de
  `FREQUENCY_BUCKETS`), que `frontend/src/lib/volume-classes.ts` consome em vez de listar as colunas à mão.
- **Fatia por item** (`item_shares`, payload v5): `MethodologyItemInput.item_index` (posição no corpo
  recebido) faz o motor devolver, por item do Modo B, `ItemDirectShare` com um `ItemGroupShare` por bloco:
  `direct_sets = (sets_count + equivalente da técnica) × peso do modo` (a mesma conta do `_add` do bloco),
  `is_primary`, `weight` e a fatia (`slice_group_*`). O eco (`slot_sequence`, `item_index`, `item_kind`,
  `mapping_id`, `muscle_group_id`) é o das listas por item do preview de ativação. **Espelho removido:**
  `frontend/src/lib/methodology-card-share.ts` não recalcula mais séries por grupo primário; o card
  (`EquipmentSeriesItemCard`) só exibe `item_shares` casado por `buildItemDirectShareLookup`. O peso e o
  papel (`is_primary`) de cada `ItemGroupShare` vêm de `block_contributions`, a mesma junção por bloco do
  `_add`.
- **Não calibrada:** `MethodologyRange.uncalibrated` e `VolumeClassSpec.weekly_uncalibrated` saem de
  `copied_from_counting_mode` (só nas filhas da 073, lidas por `load_volume_classes`). O bloco ganha
  `uncalibrated_ranges` quando a faixa do bucket ou a meta semanal USADA é cópia.
- **Comparação entre modos:** `compare_counting_modes(avaliações, modo_atual)` (puro) põe o mesmo Modelo
  lado a lado por bloco (séries da janela, bucket, status, faixas, selo) e marca `status_changed` /
  `bucket_changed`; entram os blocos com trabalho contado em algum modo. Serviço:
  `methodology_volume_service.evaluate_template_all_modes` (uma transação; contribuições lidas uma vez com
  os papéis do modo mais largo, faixas por modo). Rota: `GET /workout-plan-templates/{id}/methodology-volume/counting-modes`.
- Texto do aviso do Modelo (`warnings_from_evaluation`) usa a unidade do modo.

**CRUD admin (`muscle_volume_classes.py`).** `POST /muscle-volume-classes/{id}/copy-from-default?counting_mode=`:
ação EXPLÍCITA (botão "Copiar de Só primários" da aba), recusa o modo default (422) e só preenche, no modo
pedido, o bucket sem faixa e a meta ausente (`INSERT … SELECT … NOT EXISTS`, a forma do seed da 073), com
`copied_from_counting_mode` = default. Nunca sobrescreve nem apaga.

**Telas (só consomem).** Editor do Modelo: seletor "Contagem do volume" (Modo B), hidratado do Modelo, do
rascunho (067) e do rascunho da IA, enviado no PUT/POST, no rascunho e no corpo do preview. Geração por IA
(Periodização): o mesmo seletor no modal, `volume_counting_mode` só no Modo B (o backend sem o campo o
ignora: Pydantic com `extra` default). Diagnóstico: seção "modos lado a lado" com aviso de não calibrada.
Aba admin: alternância de modo, faixas/meta por modo, selo "cópia — não calibrada" e o botão de copiar.
Textos de unidade (painel flutuante, tabela, card, resumo recolhido, diagnóstico) vêm de `sets_unit_label`
/ `counting_mode` do payload. O realizado (Ativação 7 dias) não muda neste lote (Lote 4).

**Guarda.** `modo_contagem_volume` passa a valer também para `.ts/.tsx` e pega `'primary_only'` além de
`'primary_secondary'`: o FE nunca escreve o valor do modo (lê do JSON gerado ou do payload). Allowlist
continua vazia.

**Impacto em produção.** Modelos em `primary_only` (todos hoje): números, status e avisos idênticos; o
payload ganha só campos aditivos (`sets_unit_label`, `item_shares`, `uncalibrated_ranges`), e o card mostra
os mesmos números de antes (agora vindos do backend). Rotas novas (comparação e cópia) sem efeito até
alguém as usar. Sem migração nova.

## 41. Modo de contagem do volume por Modelo — Lote 4: progressão e realizado pelo Modelo da sessão (2026-10-16)

**Regra do usuário.** O teto de ativação da progressão (`ActivationCeiling`: `activation_max_reached`
e a trava de ADD_SET `BLOCKED_VOLUME_IN_RANGE`/`BLOCKED_ACTIVATION_MAX`) e a "Ativação 7 dias"
(`GET /analytics/methodology-realized`) contam o volume no modo do **Modelo que originou a sessão**.
Sessão sem Modelo usa `primary_only`. Não há replay.

**Como a sessão liga ao Modelo.** atividade (`activities.workout_plan_id`) → ficha (`workout_plans`,
`template_id` da 009 e `template_version_id` da 066) → versão por `template_versions.plan_version_expr`
→ `workout_plan_template_versions.volume_counting_mode`. É a mesma regra de todos os leitores por data
(adesão, motor do dashboard, relatório):
1. vale a versão gravada na ficha (066), a que o usuário viu na geração;
2. numa ficha antiga, sem versão gravada, vale a versão vigente no fim do `plan_date` (dia do usuário
   levado ao relógio do banco);
3. por fim, vale a vigente.

Treino livre, ficha sem Modelo ou versão não encontrada viram `NULL`, e `NULL` vira o default.

**Decisão aceita (16/10, revisão do PR #553): vale a versão gravada na ficha.** "O Modelo que originou
a sessão" é a versão de que a ficha nasceu (066), não a vigente no dia do treino. Uma ficha gerada na
v1 e treinada depois da troca para a v2 conta na régua da v1. A vigente no `plan_date` só entra para
ficha antiga, sem versão gravada.

**Dono — `intelligence/methodology_volume.py`.** Regras puras novas:
- `counting_mode_or_default(valor)`: modo de UMA sessão. `NULL` ou vazio vira `primary_only`.
- `counting_mode_for_window(modos em ordem)`: a janela inteira usa UM modo, o da sessão mais recente até
  a data avaliada. O último da lista vence, e uma janela sem sessão fica no default. Os modos nunca se
  misturam dentro da janela.
- `counts_as_frequency_session(tem_primary, frações_de_secundário)`: a sessão (slot do Modelo ou dia do
  realizado) conta na frequência se tem PRIMARY ou se as frações somam pelo menos
  `SECONDARY_SESSION_MIN_SETS`. O motor do Modelo passou a usar esta função no lugar da expressão inline
  (mesmo resultado; paridade nos testes do motor). O realizado usa a mesma.

**Service do dono — `methodology_volume_service.py`.** Cada leitura do modo é UMA consulta por janela:
- `load_session_counting_modes(cur, activity_ids)` devolve `activity_id → modo` em um statement;
- `load_counting_mode_at(cur, user_id, dia)` devolve o modo da sessão mais recente até o dia. São as
  atividades com exercício mapeado, as mesmas do realizado. É um statement em duas etapas:
  1. uma subconsulta escolhe primeiro o `a.id`, só com `activities` do usuário, `ORDER BY … LIMIT 1`;
  2. só essa linha faz o JOIN com a ficha e a versão.

  O filtro "tem exercício mapeado" é uma subconsulta escalar correlata (`(SELECT 1 … LIMIT 1) IS NOT
  NULL`), e não `EXISTS`. O MySQL 8 transforma o `EXISTS` em semijoin e materializa os exercícios
  mapeados de TODOS os usuários.
- `load_volume_contributions_for_modes(cur, ids, modos)`: uma leitura com a união dos papéis dos modos
  (`methodology_volume.counted_roles_for_modes`). Cada modo filtra o seu no motor.
- **EXPLAIN no DEV** (dados de produção do usuário 1, 77 atividades, MySQL 8.0.46, só leitura, 16/10;
  a 073 não está no DEV, então a coluna do modo foi trocada por `v.id`, com o mesmo plano):
  - `load_counting_mode_at`: activities por `idx_activities_user` (77 linhas, filesort), exercises por
    `idx_exercises_activity_sequence` (dependente, 1 linha por atividade), e ficha e versão por
    PRIMARY (`const`). A subconsulta leva 2,1 ms e é resolvida antes da execução.
  - Com `EXISTS`, o plano tinha `MATERIALIZED` nos 565 exercícios mapeados da tabela inteira; por isso
    a forma correlata.
  - `load_session_counting_modes` com 60 ids: activities, workout_plans e versões por PRIMARY, em
    2,8 ms.

**Consumidores (recebem o modo pronto):**
- `progression_state.load_activation_ceiling(conn, exercise_ids, sessions)` faz um número fixo de
  statements:
  - o modo das sessões (1);
  - as contribuições cruas na união dos papéis dos modos presentes (1, por
    `load_volume_contributions_for_modes`);
  - os grupos (1);
  - as faixas, 2 por modo presente.

  Sem sessão ponderada, as leituras são as de antes do Lote 4 mais a do modo. `build_session_inputs`
  passa as sessões.
- `ActivationCeiling` guarda `session_modes` e `volume_classes_by_mode`. `mode_for(janela, sessão)` é o
  modo da sessão avaliada, porque a janela do avaliador termina nela. Um modo sem cadastro carregado
  não avalia nada: não há teto, e a faixa de outro modo nunca é usada.
- `GroupActivation.counting_mode` vai para `evidence_json.activation.counting_mode`. Decisões gravadas
  antes do Lote 4 não têm o campo: elas registram a régua da época (`primary_only`) e não são
  reescritas. O avaliador e o `progression_engine` não mudaram, porque o modo chega pelo probe.
  `ENGINE_VERSION` fica `progression-v2.0.0`: a convenção do motor de progressão nunca subiu essa
  versão por mudança de regra (a evidência carrega o que mudou), e `counting_mode` na evidência é o
  marcador.
- `VolumeService.get_daily_muscle_activation(counting_mode=…)`: `direct_sets` usa o peso do motor
  (`counted_contributions`, pelo helper `_counted_by_group_name`). O filtro de papel escrito à mão saiu
  dali.
  - Em `primary_only` a linha é bit a bit a de antes, sem campo novo.
  - Nos outros modos a linha ganha `direct_secondary_sets`, a parte de SECONDARY que decide a
    frequência.
  - `sets_count` (`equivalent_sets`, régua P+S+E) não muda com o modo.
  - **Fatia + pai, fora de `primary_only`:** o endpoint passa `block_of` (`methodology_realized.block_map`,
    pela cadeia de `block_of_group`). Os grupos do MESMO exercício no mesmo bloco contam uma vez, com o
    maior peso, por `methodology_volume.block_contributions_by_member`. Essa função é a junção de
    `block_contributions`, entregue no membro de maior peso, para o realizado somar os membros e chegar
    ao mesmo número do planejado. Grupo fora do cadastro vira bloco próprio. Teste:
    `test_realizado_ponderado_conta_fatia_mais_pai_como_o_planejado` (remada com PRIMARY na fatia e
    SECONDARY no pai: 4 séries no bloco, nunca 4 + 2).
  - **Em `primary_only` a junção continua por grupo (paridade bit a bit).** Divergência latente, de
    código: um exercício com PRIMARY em dois grupos do mesmo bloco somaria 2× no realizado e 1× no
    motor. Ela não se manifesta nos dados. No DEV, com os dados de produção do usuário 1 (16/10, só
    SELECT), os únicos exercícios com contribuições contadas no mesmo bloco são 38, 81 e 110. Os três
    são PRIMARY + SECONDARY, e o SECONDARY não conta em `primary_only`. Não foi alterado; fica
    registrado para decisão.
- `methodology_realized.evaluate_realized_window` usa `counts_as_frequency_session` para dizer se o dia
  conta e para o status da célula. A métrica vai para **`realized-v2`**, e o endpoint devolve
  `counting_mode`, carrega as faixas do modo e pede a ativação diária no modo. A fixture de paridade
  (`frontend/src/lib/methodology-realized.fixture.json`) mudou só em `metric_version` e
  `counting_mode`. Os itens são idênticos, e essa é a prova de paridade do `primary_only`. A tela é do
  Lote 3.

**Fase do grupo (D2) continua só pelo primário.** `progression_state.load_primary_groups` não lê o
modo. A fase e a trilha pertencem ao grupo PRIMARY do exercício: isso define quem progride, e não é
volume. O modo só muda o teto de ativação e a trava de ADD_SET.

**Recuperação continua fora (§3.2).** `test_recuperacao_fatigue_weight_todos_os_papeis.py` varre o
`volume_service.py` inteiro (módulo, classe e funções) e só tira da varredura o corpo das duas funções
do realizado, `get_daily_muscle_activation` e `_counted_by_group_name`. Nenhuma das duas lê
`fatigue_weight` nem a dose. O teste também falha se:
- uma função de recuperação (dose, `fatigue_weight`, `recovery`) chamar uma das duas;
- os módulos de recuperação (`muscle_recovery_service`, `recovery_read_model`, `recovery_projection`,
  `session_fatigue_reader`) citarem o modo ou as duas funções.

Ele prova ainda que `sets_count` é igual nos dois modos. `with_secondary` pergunta ao motor
(`counts_secondary(mode)`).

**Guarda.** Padrão novo `modo_contagem_da_sessao`, com allowlist vazia. Proíbe ler a coluna
`volume_counting_mode` à mão em `intelligence/`, `domain/`, `progression_state.py`, `analytics.py` e
`analytics_dashboard.py`. Quem resolve a versão e o modo é o service dono. O CRUD do Modelo
(`workout_plan_templates.py`, `template_versions.py`, `models.py`) grava a coluna e fica fora do escopo.
`modo_contagem_volume`, `faixas_sem_modo` e `peso_secundario` continuam vazias.

**Impacto em produção.**
- Nenhum Modelo está em `primary_secondary`: a 073 ainda nem está em produção, e o default é
  `primary_only`. Para todo usuário, então:
  - o teto de ativação, a trava de ADD_SET e o realizado ficam idênticos;
  - a evidência ganha `"counting_mode": "primary_only"`;
  - o payload do realizado ganha `counting_mode` e passa a `realized-v2`.
- Prova por paridade:
  - `test_modo_contagem_volume_l4.py::test_paridade_bit_a_bit_sem_modelo_ponderado`: a leitura nova é
    igual à de antes do Lote 4;
  - a fixture do realizado tem os itens iguais;
  - `test_daily_muscle_activation_direct_sets.py` mantém o contrato e as chaves.
- Custo: +1 SELECT por avaliação de progressão (ao finalizar o treino e no replay) e +1 SELECT por
  `GET /analytics/methodology-realized`, ambos indexados. Nenhuma escrita nova, nenhuma migração.
- **Checklist da promoção:**
  1. **A 073 aplicada antes ou junto, com `RUN_DB_MIGRATIONS=true`.** Este código NUNCA sobe sem a 073:
     as leituras selecionam `volume_counting_mode` e as filhas por modo, e sem elas o realizado dá erro
     500. Na progressão, a leitura do modo em `build_session_inputs` falha, e `on_session_finish`
     captura o erro e registra `progression.shadow_failed` no log, sem gravar decisão.
  2. Smoke: `GET /analytics/methodology-realized` devolve `counting_mode == "primary_only"` e
     `metric_version == "realized-v2"`, com os itens iguais aos de antes da promoção.
  3. Smoke: uma decisão de progressão nova (finalizar um treino) tem
     `evidence_json.activation.counting_mode` gravado (`"primary_only"`), e as decisões antigas ficam
     sem o campo.

## 42. Modo de contagem do volume por Modelo — Lote 5: geração por IA e validador (2026-10-16)

**De onde vem o modo na geração.** A Periodização gera um Modelo **novo**: o pedido
(`POST /workout-plan-templates/periodization-drafts`) não tem Modelo de origem, e `generation_runs`
(054) é da geração de **fichas**, fora deste fluxo. O modo vem só do pedido:
`PeriodizationDraftIn.volume_counting_mode`, opcional, validado pelo enum do motor
(`models.counting_mode_value`). Ausente = `DEFAULT_VOLUME_COUNTING_MODE` (`primary_only`).
- Um ponto só resolve o modo: `periodization/context.request_counting_mode`. Contexto, validador e
  complemento leem dele.
- O pedido inteiro, com o modo, fica em `template_generation_logs.request_json` (038).
- O rascunho devolvido leva o modo (`WorkoutPlanTemplateIn.volume_counting_mode`). Pedido sem modo
  devolve `None`, e a criação usa o default, como antes.
- A tela (Lote 3) só precisa mandar o campo no corpo do pedido.

**Prompt (`context.py`, `PROMPT_VERSION = periodization-v11`).**
- `_COUNTING_RULES` e `_CHECKLIST_COUNT`, por `VolumeCountingMode`, substituem a frase "conta por
  inteiro para o grupo PRIMARIO (EXG-014)" de `_MODE_RULES["equipment_series"]` e o item 2 de
  `_FINAL_CHECKLIST`.
- Em `primary_only` o texto é o da v10, letra por letra. O snapshot renderizado prova isso: o
  `prompt_sha256` dos 5 casos antigos não mudou; mudaram só o `body_sha256` e o `prompt_cache_key`,
  pela versão.
- No modo ponderado, `_counting_rule` interpola do motor o modo, o default, os papéis contados
  (`COUNTED_ROLES_BY_MODE`) e o limiar `SECONDARY_SESSION_MIN_SETS`. Nenhum peso está escrito no
  texto.
- No modo ponderado, cada par do catálogo da academia ganha `volume_contributions`:
  `[{muscle_group_id, weight, primary}]`, calculado por `block_contributions` sobre o cadastro,
  com a herança do canônico (`methodology_volume_service.load_inherited_contributions`, extraído
  de `load_methodology_inputs`, uma leitura). Em `primary_only` o catálogo sai intocado e não há
  leitura nova.
  - Seguimento do #550: `muscle_group_id` é o id do **bloco**. Fatia e pai no mesmo bloco viram uma
    entrada só, com o maior peso, PRIMARY se algum for: é a junção do motor, para a LLM não somar
    em dobro. No caso de snapshot, 28 pares do catálogo colapsaram.
  - O peso é arredondado com `methodology_volume.SETS_DECIMAL_PLACES`, constante nova do motor
    (as mesmas 2 casas de `_num`).
  - O texto e o payload de `primary_only` não mudaram, então o `PROMPT_VERSION` continua `v11`.
- A TABELA da metodologia e as metas derivadas vêm das faixas do modo
  (`load_methodology_catalog(conn, modo)` → `load_volume_classes(cur, modo)`), as mesmas que o
  validador aplica.
- Caso novo de eval/snapshot: `modo_b_6_dias_abc_ponderado`, o mesmo pedido de `modo_b_6_dias_abc`
  no modo ponderado. Os pesos são os do cadastro do DEV (cópia de produção, 16/10).

**Validador e complemento.**
- `validate_draft` resolve o modo uma vez e o repassa a `_apply_volume_complement` e a
  `_check_methodology`. Os dois chamam `load_methodology_inputs(ids, modo)`, ainda uma leitura só.
- `volume_complement` já media pelo motor no modo das leituras (`_measure`). Agora
  `contributions_of`/`blocks_of` usam `methodology_volume.block_contributions` (novo no motor: os
  pesos de `counted_contributions` levados ao bloco, fatia + pai com o maior peso; o laço de
  `evaluate_methodology_volume` chama a mesma função). Assim, somar série num
  composto conta também no secundário, e as travas medem isso, inclusive "nenhum grupo passa a
  ABOVE".
- Para não mudar a frequência, o complemento só age em slot com trabalho PRIMARY do grupo
  (`direct_exercises > 0`). Ele prefere item ou par em que o grupo é PRIMARY ao item ou par em que é
  só secundário (rank 0 exclusivo, 1 PRIMARY com outros, 2 só secundário).
- Em `primary_only` todo grupo contado é PRIMARY, então ordem e decisões são as de antes. A paridade
  está em `test_modo_contagem_volume_l5_geracao.py`: linhas cruas com secundário e estabilizador dão
  o mesmo complemento que o conjunto da v4.

**RAG.** A EXG-014 (fonte metodológica) **não** foi reescrita: ela vale para `primary_only`.
`primary_secondary` é opção do usuário, com faixas próprias (§39), e o prompt do modo diz isso.
Divergência registrada, sem mudança neste lote: `rag/markdown/PROGRESSAO.md` ("Volume semanal por
grupo muscular") descreve a régua ponderada das metas de ativação (`VolumeService`: primário
inteiro, secundário em fração do catálogo). Em `primary_only` o prompt do Modo B manda contar só o
primário, e a regra do modo prevalece, como já acontecia na v10. No modo ponderado o texto do RAG
fica próximo da regra, mas não igual: lá o primário também usa o `stimulus_weight` cadastrado.

**Fica para os outros lotes.** A docstring de `VolumeCountingMode` e a lista "continuam em
`primary_only`" do §39 ainda citam o Lote 5 como pendente. Os Lotes 3 e 4 editam esses trechos em
paralelo, e a limpeza fica para o último que mesclar.

**Guarda.** Nenhum padrão novo. `modo_contagem_volume`, `peso_secundario` e
`numeros_prompt_volume` continuam com a allowlist vazia: o modo vem do enum, o limiar é
interpolado e o peso vem do cadastro.

**Impacto em produção.**
- Pedido sem modo, que é tudo o que a tela atual manda: prompt idêntico ao da v10 (mesmo
  `prompt_sha256`); validador e complemento com as mesmas leituras e decisões; rascunho igual.
- Muda só o rótulo `periodization-v11` no log e no `prompt_cache_key`, o que custa uma perda de cache
  na primeira geração.
- Sem migração neste lote: depende da 073 (Lote 2), que já está na develop.
- Modo ponderado: o prompt cresce com `volume_contributions`. No caso `modo_b_6_dias_abc_ponderado`
  são 59.849 caracteres, contra 45.871 do mesmo pedido em `primary_only`. Antes de ligar o modo na tela, vale o eval pago antes/depois
  (`docs/ia/EVALS.md`).

## 43. Validador da Periodização: rótulo do grupo, corte de excesso e zero em campo opcional (2026-10-16)

Achados do eval pago de 16/10 (prompt `periodization-v11`, 6 casos x 5 rodadas):
- a LLM rotula o item com o grupo pai/irmão do mesmo bloco ("Peitoral inferior" volta como
  Peitoral; "Costas meio" volta como Costas, e vice-versa). O validador só conferia se o par estava
  ativo, e o preview do editor, que agrupa por `item.muscle_group_id`, mostrava o exercício no grupo
  errado;
- o complemento de volume (M46) só soma. No modo ponderado a resposta saiu com 6 grupos ABOVE_RANGE
  (bíceps 16 séries/sessão, 48/semana, contra 4–5 e 12–14) e nada corrigia.

**Regra 1 — `MUSCLE_GROUP_RELABELED` (dono: `periodization/validation.py`, `_relabel_muscle_group`).**
- Par (exercício, equipamento) ATIVO na academia com `muscle_group_id` diferente do grupo sob o qual
  o catálogo o lista vira esse grupo. O grupo do catálogo é o PRIMARY do exercício no cadastro, pela
  MESMA subconsulta que monta o catálogo da LLM (`equipment_recommender._PRIMARY_GROUP_ON`, primeira
  PRIMARY ativa por `emc.id`) e que `_load_gym_pairs` já usava. No DEV, 1 de 103 exercícios tem
  duas PRIMARY; o rótulo segue a do catálogo.
- `_load_gym_pairs` passa a ler `pg.name` e a leitura de `muscle_groups` de `_check_references`
  passa a ler `name`, só para o relatório dizer "Peitoral → Peitoral inferior". Payload: slot,
  slot_name, item, mapping_id, equipment_id, exercise_name, equipment_name, from/from_name,
  to/to_name.
- Nenhum número muda: no Modo B o motor conta pelo `mapping_id` (o rótulo só é ecoado). O teste
  prova com as 19 respostas reais válidas: avaliação do motor, avisos e demais auto-fixes iguais com
  e sem o relabel. Vale nos dois modos.

**Regra 2 — `VOLUME_REDUCED` (dono: `periodization/volume_complement.py`, `_reduce_volume`).**
- Roda DEPOIS do acréscimo e uma vez só (não volta a ele). Grupo ACIMA da faixa de séries, por
  sessão e depois semanal, como o motor avalia no modo do pedido, perde uma série por passo. É um
  passo por grupo por rodada, em ordem de id.
- Candidatos: itens dos slots da janela que contam para o grupo (`block_contributions`), com mais
  séries que o piso e que o acréscimo não tocou.
- Piso: `methodology_volume._SETS_PER_EXERCISE_MIN` (3, EXG-004, dono `metrics_registry`), o mesmo do
  exercício novo e do default de séries do validador (`_sets_floor_for_reduction`). Nunca remove
  item nem zera série. Nenhum número novo.
- Ordem: não priorizados antes dos priorizados (**escolha: `prioritize` só é cortado sem
  alternativa**, nunca seria pior que deixar 48 séries/semana); PRIMARY do grupo antes de
  só-secundário; maior peso no grupo, mais séries, ordem do ciclo e do slot.
- Trava `_acceptable_cut`, simétrica à do acréscimo, medida no motor real: o excesso do alvo cai;
  nenhum eixo de nenhum grupo fica mais abaixo da faixa (WITHIN não cai para BELOW, e cortar o
  secundário não quebra o primário do item); nenhum outro eixo fica mais acima, porque o corte pode
  mudar a frequência de um secundário e, com ela, a célula da faixa.
- Intenção (**escolha**): eixo cujo ABOVE o motor marca como esperado pela intenção (severidade
  `informational` de `_severity_for`, alvo da especialização) não é cortado. `complement_volume`
  ganhou o parâmetro `intent`, repassado ao motor. A Periodização ainda não tem intenção no pedido,
  então hoje é sempre `standard`, e todo ABOVE é aviso.
- Termina: cada passo aceito tira uma série e o piso limita os passos. Há também um teto rígido de
  custo, com orçamento próprio separado dos passos do acréscimo (`MAX_STEPS`):
  `limits.COMPLEMENT_REDUCTION_MAX_EVALUATIONS` = 200 avaliações do motor por chamada, contando
  cortes aceitos e rejeitados. Se o teto estourar, o corte para onde está e o excesso restante
  continua como aviso. Rodar o complemento de novo sobre o resultado não muda nada (teste de
  idempotência).
- O que o piso não deixa fechar continua como `METHODOLOGY_*ABOVE_RANGE`. Exemplo: costas com 4
  exercícios por sessão x 3 séries = 12, contra a faixa 5–6. O excesso restante é de número de
  exercícios, e o corte nunca remove item.

**Regra 3 — `OPTIONAL_ZERO_NULLED` (dono: `periodization/validation.py`, `_null_optional_zeros`).**
- Na rodada 3 de `modo_b_4_dias_instrucoes` a LLM mandou `0` em vez de `null` em aquecimento e
  série de ajuste. O `PeriodizationOutput` aceitava, mas o `WorkoutPlanTemplateIn` (`ge=1`) recusava
  em `_to_template_in`, e o `ValidationError` escapava de `validate_draft`. O job terminava em
  exceção depois da geração paga (1 de 20 respostas do Modo B).
- Agora, ANTES de `_to_template_in`, todo campo de `OPTIONAL_POSITIVE_ITEM_FIELDS` com `0` vira
  `null`, com uma entrada por item e a lista de campos.
- A lista é DERIVADA do schema: são os campos opcionais de `WorkoutPlanTemplateSlotItemIn` com
  `Ge`/`Gt` que mantém fora o 0 e que a saída da LLM também tem. Ficam de fora `rest_seconds`, que
  `_to_template_in` sempre anula, e `sets_count`, que tem conserto próprio (`SETS_COUNT_FIXED`).
- Hoje a lista tem 9 campos: aplicações/duração da técnica; séries, repetições e descanso do
  aquecimento e da série de ajuste; pico de contração.
- Qualquer outro `ValidationError` depois do schema (por exemplo, valor negativo) vira
  `SCHEMA_INVALID` no relatório, sem rascunho. Nada escapa como exceção.

**Guarda.** Nenhum padrão novo e nenhuma entrada de allowlist. O piso é a constante do motor, e o
teto novo de avaliações é nomeado em `limits.py`, que já é dono de `COMPLEMENT_*`.

**Impacto em produção.**
- **Como foi medido:** offline, sem OpenAI. Rodei o `validate_draft` real com as leituras do DEV
  congeladas em `api/tests/unit/data/periodizacao_eval_2026_10_16.json`, e conferi no container com
  o DEV real (só leitura, rollback). Desde o M103 L2 (#667), a nota de saúde do pedido nessa fixture
  é sintética (repositório público). Os números da medição abaixo são os da época, medidos com a
  nota original.
- **Amostra:** 4 casos do Modo B x 5 rodadas = 20 respostas. A rodada 3 de
  `modo_b_4_dias_instrucoes` passou a ser medida graças à regra 3. Sem a regra 3 ela não tem
  "antes", então a coluna "antes" é esta versão sem o corte.
- **Leitura da tabela:** média e, entre parênteses, a faixa por resposta.

| caso (modo) | rodadas | ABOVE sessão antes → depois | ABOVE semanal antes → depois | BELOW sessão antes → depois | BELOW semanal antes → depois | séries no Modelo antes → depois | MUSCLE_GROUP_RELABELED | VOLUME_REDUCED |
|---|---|---|---|---|---|---|---|---|
| modo_b_6_dias_abc (primary_only) | 5 | 2,2 (2–3) → 0,8 (0–2) | 2,2 (2–3) → 1,0 (0–3) | 1,8 (0–3) → 1,8 (0–3) | 1,8 (0–3) → 1,8 (0–3) | 113,8 (104–142) → 107,8 (101–128) | 2,0 (1–3) | 6,0 (3–14) |
| modo_b_6_dias_abc_ponderado (ponderado) | 5 | 3,2 (2–6) → 2,6 (2–5) | 4,0 (3–6) → 3,0 (2–5) | 3,0 (2–4) → 3,0 (2–4) | 2,8 (2–3) → 2,8 (2–3) | 111,6 (97–151) → 103,0 (92–128) | 4,2 (2–8) | 8,6 (4–23) |
| modo_b_4_dias_instrucoes (primary_only) | 5 | 4,6 (1–7) → 1,0 (0–2) | 4,6 (1–7) → 1,2 (0–3) | 0,8 (0–3) → 0,8 (0–3) | 0,8 (0–3) → 0,8 (0–3) | 84,8 (71–112) → 74,8 (62–97) | 2,0 (1–3) | 10,0 (2–15) |
| modo_b_5_dias_sem_perfil (primary_only) | 5 | 3,0 (2–4) → 1,2 (0–2) | 3,2 (2–5) → 1,6 (1–2) | 1,0 (0–4) → 1,0 (0–4) | 1,0 (0–4) → 1,0 (0–4) | 98,2 (80–114) → 90,6 (74–106) | 0,6 (0–2) | 7,6 (5–10) |

**Totais das 20 respostas:**
- ABOVE por sessão: 65 → 28;
- ABOVE semanal: 70 → 34;
- BELOW sem mudança: 33 → 33 por sessão e 32 → 32 semanal. O corte nunca criou déficit.

O teste `test_periodization_rotulo_e_corte_volume.py::test_real_impacto_medido` trava esses números.

**Tempo e custo, medidos no container da API com o DEV real e contando o validador inteiro com as
leituras de banco:**
- Tempo MÁXIMO por resposta: 2,18 s. O pior caso foi o ponderado da rodada 1.
- Avaliações do motor (`_measure`) por resposta: até 237 no acréscimo e até 55 no corte, 264 no
  máximo somando os dois.
- Contra o teto de 200 avaliações do corte, isso dá uma folga de 3,6x.
- No pior caso o corte custa até 200 avaliações, cerca de 1,6 s ao custo medido de ~8 ms por
  avaliação.

**Margem na Lambda:**
- Orçamento: a Lambda de IA tem 180 s (`infra/lambda_ai_generators.tf`). A OpenAI pode consumir
  até 90 s (`core.OPENAI_TIMEOUT_SECONDS`), então sobram ~90 s para validar. A Lambda é a `plan-generator`, que roda a Periodização.
- Pior resposta medida: 2,18 s, cerca de 41x abaixo desses 90 s.
- Mesmo somando o pior caso do corte (+1,6 s), o validador fica abaixo de 4 s.
- Risco residual, não tratado aqui: o acréscimo tem `MAX_STEPS` = 500 passos aceitos, mas não tem
  teto para as tentativas rejeitadas. Medido: até 237 avaliações.

**Outros efeitos:**
- Modo A: nada muda, porque não há par nem complemento.
- Pedido sem rótulo trocado, sem zero e sem grupo acima da faixa: o rascunho sai idêntico ao de
  antes.
- SQL: só duas colunas lidas a mais (`pg.name`, `muscle_groups.name`). Sem migração.

**Auto-fixes já existentes, confirmados nas 5 rodadas:**
- 1x 7 slots no caso ponderado (pedido 6): `SLOT_REMOVED`;
- 3x mais de 3 priorizados no slot: `PRIORITIZE_UNMARKED`;
- 1x par fora do catálogo: `EQUIPMENT_REPLACED`.

Todos terminam sem erro e estão cobertos por teste.

## 44. Nutrição — Lote L0, fundação (2026-10-15)

Plano: `docs/nutricao/PLANO_NUTRICAO_2026-10-14.md` (§6.1, §6.3, §8.1, §13). Sem mudança de
semântica em motor existente; dois donos novos nascem com a regra e as cópias saem no mesmo PR.
Migrações 074–076 (nasceram 073–075 na branch e foram renumeradas no merge com a develop: a 073 é a
do modo de contagem do volume, §39, já aplicada em produção; o runner recusa versão duplicada).

- **Locais de medida por fita.** Os rótulos pt-BR viviam em TRÊS cópias Python
  (`body_measurements.py::MEASUREMENT_SITE_LABELS`, `dashboard/body_service.py` — que copiava para não
  importar o router —, e `"Cintura"` solto em `intelligence_package.py` e `markdown_report_service.py`) e
  UMA no TS (`dashboard-body.ts::MEASUREMENT_SITE_LABELS`, com a lista em `dashboard-body-types.ts`).
  Dono novo `domain/body_measurement_sites.py` (Python puro): `MEASUREMENT_SITES` na ordem do ENUM
  (047 + os três da 075 no fim: `neck`, `forearm_l`, `forearm_r`), `SITE_LABELS`, `site_side`,
  `export_for_frontend()`. A API e a engines importam; o FE lê o bloco `body_measurement_sites` de
  `motor-constants.generated.json` por `lib/body-measurement-sites.ts` (paridade em
  `body-measurement-sites.test.ts`), e `MedidasFitaSection` (opções do seletor), `DashboardCorpoClient`
  (título da cintura) e `DashboardOverviewClient` (linha da cintura) consomem dele. A dica do card
  "Outras medidas" do `DashboardCorpoClient` não lista locais ("Demais locais medidos por fita"): a
  lista à mão que estava lá ignorava pescoço e antebraços (revisão do #556). Guarda nova
  `rotulo_site_medida` (allowlist vazia; desde 2026-10-16 pega também crase e texto JSX).
- **Origem da kcal do treino.** `domain/calories_source.py`: vocabulário `manual`/`intervals`
  (coluna `activities.calories_source`, migração 076) e `resolve_calories_source` na LEITURA —
  coluna manda; senão `intervals` quando a kcal é igual à de `activity_intervals_link` (o sync do
  Intervals preenche por `COALESCE` e não grava origem); senão `None`. `GET /activities/{id}` devolve
  código e rótulo prontos; a tela do registro do treino só exibe e edita. A GRAVAÇÃO também é do
  dono (revisão do #556): `PATCH /meta` lê a kcal do link e grava `source_on_manual_write(kcal,
  kcal_do_link)` — `None` ao limpar (`null` limpa kcal e origem), `None` quando a kcal digitada é a do
  link em 0,01 (a leitura volta a inferir `intervals`), `manual` no resto —, e a tela fecha a edição
  sem PATCH quando o valor não mudou (antes, salvar sem mudar trocava 412,37 do Intervals por 412
  "Manual"). Guarda nova `origem_kcal` (allowlist vazia). Decisão do Tech Lead: nada no `finish` do
  Modo Treino (o Intervals preenche no dia seguinte; um valor manual bloquearia o `COALESCE`).
- **Perfil.** 074 só cria `users.height_cm` e `users.activity_level`; `/auth/me` só EXPÕE os
  quatro campos (`sex` como está gravado, hoje `M`/`F`). Validação (altura 100–250, vocabulário de
  sexo e de nível), PATCH/telas e o backfill de `sex` para o vocabulário do motor ficam para o L2,
  quando o motor do L1 (`domain/nutrition/anthropometrics.py`) existir — as telas atuais de perfil
  e de administração gravam `M`/`F` e regravariam um valor convertido. Nenhuma faixa nem
  vocabulário foi escrito fora do dono.
- **Regenerar:** `cd api && python tests/unit/test_m76_lote1b_espelhos_fe.py --gravar`.

## 45. Nutrição — Lote L1: motor, guarda e mapa, sem consumidor (2026-10-16)

Plano: [`docs/nutricao/PLANO_NUTRICAO_2026-10-14.md`](../nutricao/PLANO_NUTRICAO_2026-10-14.md) §3, §7 e §14.
Tabela regra → dono: §3.7.

**Motor — `api/app/domain/nutrition/`** (16 módulos, `ENGINE_VERSION = "nutrition-v1"`). Python puro, sem
I/O nem relógio, constantes `Final`, dataclasses `frozen=True` com `to_dict()`, `None` + código em
`limitations` quando não dá para calcular. Nenhum consumidor em runtime neste lote: as rotas, a
materialização (`compute_daily_target`, E2), as telas e os prompts chegam do L2 em diante.
`anthropometrics` tem **contrato público estável** (`Sex`, `normalize_sex`, `ActivityLevel`,
`normalize_activity_level`, `ACTIVITY_FACTORS`, `NON_EXERCISE_ACTIVITY_FACTORS`, `HEIGHT_CM_RANGE`,
`age_years`, `bmi`, `Subject`): o L0 valida o perfil (`users.py`) e o L2 monta o `Subject` com esses nomes.

**Decisões registradas.**
- **Sem Keytel (D-K, 2026-10-15).** O Intervals entrega kcal em 150 de 150 atividades de musculação
  (`WeightTraining`) medidas em produção. "FC sem kcal" não ocorre, e a fórmula de Keytel foi validada em
  aeróbio. A kcal do treino segue a prioridade de natureza `reported` → `met`. MET é o único fallback
  (`ESTIMATED`, qualidade baixa, calculado na leitura e nunca gravado, E6). A guarda não tem o padrão
  `keytel` (o plano previa 12; são 11).
- **Natureza × proveniência.** A proveniência da kcal informada (digitada à mão × sync do Intervals) tem
  dono próprio, `api/app/domain/calories_source.py`, que chega pelo PR do L0 aberto em paralelo. O motor
  de nutrição só consome o número `activities.calories_kcal`, qualquer que seja a proveniência, e
  classifica a NATUREZA (`reported` observada × `met` estimada). Para não criar vocabulário de
  proveniência neste pacote, `TrainingKcalSource` virou `TrainingKcalNature`, o campo `TrainingKcal.source`
  virou `kcal_nature`, `TRAINING_KCAL_SOURCE_PRIORITY` virou `TRAINING_KCAL_NATURE_PRIORITY`, o rótulo
  "Informada (Intervals ou manual)" virou "Informada" e o bloco exportado usa `training_kcal_natures`.
  A docstring de `energy_expenditure.py` registra a separação. O teste
  `test_natureza_nao_e_proveniencia` recusa nome com `source`, `intervals` ou `manual` nesse módulo.
  Pelo mesmo motivo, `BodyFatSource`/`BodyFat.source`/`body_fat_sources` viraram
  `BodyFatMethod`/`method`/`body_fat_methods`: bioimpedância × circunferência é MÉTODO, não proveniência.
- **Natureza e qualidade comuns (revisão do #557, opção B).** O L1 tinha `Nature`/`Quality` próprios, em
  minúsculas, paralelos ao `DataNature`/`DataQuality` do dashboard. Os dois enums foram extraídos de
  `intelligence/dashboard/contracts.py` para o módulo puro `domain/data_nature.py` (§3.1), com os mesmos
  valores em maiúsculas. `contracts.py` reexporta o mesmo objeto, então os 17 importadores e a API não
  mudam (testes `test_domain_data_nature.py` e `test_dashboard_contracts.py`). A nutrição usa esses enums
  em `TrainingKcal`, `AdaptiveTdee` (janela insuficiente agora é `INSUFFICIENT`, não `LOW`) e
  `reconcile`. A natureza dos totais da refeição precisa de `MIXED`, que o vocabulário comum não tem:
  `meal_math.MealNature` é próprio, e DERIVED/ESTIMATED reutilizam os valores de `DataNature`.
  `NATURE_LABELS`/`QUALITY_LABELS` e as chaves `natures`/`nature_labels`/`qualities`/`quality_labels`
  saíram do bloco `nutrition`, porque esse vocabulário não é da nutrição. Os rótulos de natureza
  continuam escritos à mão em `frontend/src/lib/dashboard-status.ts` (`DATA_NATURE`): cópia
  pré-existente, registrada no §3.1, fora deste lote.
- **Defaults de desenho, sem fonte bibliográfica** (a confirmar pelo usuário, marcados nas docstrings):
  `MIN_WEIGHT_SPAN_DAYS` = 7 e `RECONCILE_WEIGHT_BY_QUALITY` (`adaptive_tdee`), `TOKEN_JACCARD_WEIGHT`
  (`food_text`), `ATWATER_TOLERANCE_SHARE` 15% e `LABEL_SERVING_TOLERANCE_SHARE` 10% (`meal_math`). A RDC
  429/2020 define o formato e os campos do rótulo, não a tolerância de 10%.
- **Semana na segunda.** `weekly_budget.week_start` é o dono da regra para a nutrição. Não há outra
  função de início de semana em `api/app`; `time_windows` dá o dia do usuário e o resto do app calcula a
  semana a partir da segunda (`training_load_service`, SQL semanal de `volume_service`), então as duas
  coincidem (§3.1, linha "Agora, hoje, semana").

**Correções que os testes do L1 revelaram (motor sem consumidor, sem impacto em produção).**
- `weekly_budget.weekly_adherence`: 2.100 de 2.000 kcal (exatamente +5%) caía fora da tolerância porque a
  razão em float é 1,0500000000000000444. Os limites passam a ser comparados em kcal/g
  (`|ingestão − alvo| ≤ 5% × alvo`; `proteína ≥ 90% × alvo`).
- `food_text.SYNONYMS`: "galinha" deixou de ser sinônimo de "frango". "Ovo, de galinha, inteiro, cozido"
  virava "ovo frango inteiro cozido", e uma busca por "ovo de galinha cozido" dava 0,77 para "Frango
  cozido" (agora 0,38, abaixo do mínimo).
- `daily_target`: a pausa de dieta e o refeed avisavam `param_out_of_range:protein_g_per_kg` falso: a
  proteína do cutting (2,6 g/kg de MLG) era comparada com a faixa da manutenção. O aviso agora usa os
  parâmetros publicados do plano.
- `daily_target`: dia de treino sem kcal nem duração somava 0 ao TDEE em silêncio. Agora leva
  `training_kcal_unavailable`, só no modo explícito (no modo embutido o treino já está no fator).
- `adaptive_tdee`: a tendência do peso era a diferença entre o primeiro e o último ponto de uma média
  exponencial (α 0,3), dividida pela janela inteira. Com pesagens só nos dias 7 e 13 de 14 dava −0,3 kg
  no lugar de −1 kg, dividido por 14 dias em vez de 6. Com pesagem diária, o atraso da média encolhia a
  variação em ~2,3 dias. **Escolha de método:** a tendência passa a ser a inclinação por mínimos
  quadrados das pesagens válidas contra o dia da janela (`weight_trend`, kg/dia). Ela não tem atraso,
  usa o tempo real entre pesagens e dá o mesmo número com pesagem diária ou semanal. `EMA_ALPHA` saiu.
  Entraram `MIN_WEIGHT_SPAN_DAYS` = 7 (pesagens cobrindo ao menos uma semana; literal próprio,
  **default de desenho**, não está no plano) e o erro `LIMIT_SERIES_LENGTH_MISMATCH` (as duas séries
  precisam ter um elemento por dia).

**Guarda — 11 padrões novos** (§5): `tmb_formula`, `fator_atividade`, `kcal_por_grama`, `met_compendium`,
`navy_gordura`, `densidade_energetica`, `faixa_proteina`, `ffmi_normalizado`, `pausa_dieta`,
`rotulo_estrategia`, `kcal_treino_prioridade`. Cada dono guarda a regra em constante nomeada. Por isso
cada regex pega o literal da fonte E a constante do dono redefinida com número, e
`test_a_guarda_enxerga_a_regra_no_dono` vê a regra em todos os donos. Ajustes ao código real, em relação
à tabela do §7 do plano:
- `kcal_por_grama`: o dono é só `macros.py`. `meal_math` consome `kcal_from_macros`, e `* 4.184` (kJ) e
  `'1 / 4'` (grid de CSS) não casam.
- `faixa_proteina`: vale para o repo inteiro (o plano pedia nutrição + RAG de nutrição). HYP-031/032 do RAG
  geral foram conferidos e não têm g/kg, então a allowlist nasce vazia.
- `met_compendium`: também em RAG (`md`).
- `rotulo_estrategia`: também Python (rótulo em prompt é cópia). Allowlist com 2 achados pré-existentes
  FORA de nutrição: `report/markdown_renderer.py` (1, `INTENT_LABELS["maintenance"]`) e
  `lib/methodology-volume.ts` (2, `METHODOLOGY_INTENT_OPTIONS`). São a INTENÇÃO da metodologia de volume
  (§3.5), outro vocabulário que coincide na grafia, e divergem entre si ("Cutting" × "Cutting
  (definição)"). É o inventário inicial: fica como está e sai quando o backlog **M84** criar o
  `INTENT_LABELS` no dono futuro (`intelligence/methodology_volume.py`, linha nova no §3.5).
- `kcal_treino_prioridade`: fallback de exibição para placeholder (`?? '—'`, `or 0`, `?? null`, `or None`)
  não é prioridade e não casa. Isso evita falso positivo nas telas do L0, que mostram a kcal.
- Os padrões com número genérico (`fator_atividade`, `kcal_por_grama`, `met_compendium`, `pausa_dieta`)
  usam `ESCOPO_NUTRICAO`. É o motor, mais os arquivos que os lotes L2+ vão criar
  (`api/app/nutrition_*.py`, `intelligence/dashboard/nutrition_service.py`, telas `(app)/nutricao/`,
  `components/nutrition/`, `lib/nutrition-*`, `rag/markdown/NUTRICAO/`). `met_compendium` também
  cobre `workout_sessions.py`. Para isso, `_no_escopo` passou a aceitar glob com `*` (`fnmatch`), com o
  teste `test_escopo_aceita_glob_para_arquivos_de_lotes_futuros`.

**Ampliações da revisão do #557** (todas com 0 ocorrência nova no repo; a allowlist não cresceu):
- `kcal_por_grama`: número antes do operador (`4 * protein`, `9 × (`), tabela por macro (`fat: 9`,
  `protein: 4`; macros de alimento com decimal, como `protein_g: 4.5`, não casam), "4 kcal/g" / "9 kcal
  por grama" e `md`. No RAG vale só a `regex_md` de kcal/g (campo novo `Padrao.regex_md`, testado por
  `EXEMPLOS_MD` e `test_md_usa_a_regex_propria_quando_ha`).
- `fator_atividade`: tabela por nível (`moderate: 1.4`, `ActivityLevel.LIGHT: 1.3`, `['LIGHT']: 1.3`) e `md`.
- `faixa_proteina`: g/kg inteiro ("2 g/kg") e as chaves das faixas do formulário com número
  (`protein_g_per_kg`, `fat_kcal_share`, `surplus_share`, `weekly_rate_share`, `carb_min_g_per_kg`,
  `max_deficit_share`, `min_intake_bmr_multiple`, nomes reais do `form_ranges`).
- `met_compendium`: `0,0175` (= 3,5/200). `navy_gordura`: forma métrica (`1,0324`, `0,19077`,
  `0,15456`, `1,29579`, `0,35004`). `rotulo_estrategia`: texto JSX (`>Cutting<`).
  `kcal_treino_prioridade`: ternário Python `calories_kcal if … else <estimativa>`.
- `ESCOPO_NUTRICAO` ganhou `api/app/external_food_sources.py` (L3). A seção de nutrição do relatório
  (L9) e o `context_service` (L8) entram quando esses lotes criarem o código (§5).

**Espelho FE.** `export_for_frontend()` gera o bloco `nutrition` (34 chaves: códigos, rótulos, faixas de
formulário, campos da RDC 429, porções caseiras) em `motor-constants.generated.json`, pelo gerador
`test_m76_lote1b_espelhos_fe.py --gravar`. `frontend/src/lib/nutrition-constants.ts` só tipa e lê o bloco,
sem número nem rótulo escrito à mão. `nutrition-constants.parity.test.ts` confere a paridade com o JSON.
Nenhum coeficiente de cálculo vai para o FE: o preview de macros é chamada à API (E9).

**Riscos para os lotes seguintes.**
- **L8, ciclo de carbo × treino explícito.** `distribute_week` preserva 7 × alvo sobre UMA meta-base. No
  modo `EXPLICIT_TRAINING`, a meta-base de cada dia já inclui a kcal do treino daquele dia, e
  `compute_daily_target` aplica o multiplicador sobre a base do próprio dia. A soma semanal passa a ter
  nH·Δ·(mH − 1) g a mais de carbo (Δ = carbo do treino). Medido no motor: bulking, homem de 80 kg, 4
  treinos de 60 min (MET) → Δ = 60,6 g, e a semana fica com 2.770,6 g em vez de 2.720,4 g (+50,2 g). O L8 deve
  ciclar sobre a média semanal das metas-base (ou ciclar só no modo embutido). O teste
  `test_ciclo_de_carbo_soma_sete_vezes_a_meta_sem_ciclo` fixa o invariante com a base igual nos 7 dias.
- **L3/L5, nota do matching.** `food_text.score` penaliza nome de catálogo longo: "arroz branco cozido" ×
  "Arroz, tipo 1, cozido" dá 0,57 (abaixo do mínimo de 0,6). Calibrar contra a TACO real no L3/L5 (o
  limiar é do dono; a mudança é no `score`).

**Impacto em produção.** Nenhum. É um pacote novo sem consumidor em runtime
(`git grep -n "domain.nutrition" -- api/app` só mostra o próprio pacote). O JSON gerado do frontend ganha o
bloco `nutrition`, lido só pelo teste de paridade.

## 46. Nutrição — Lote L2a: perfil antropométrico (2026-10-17)

Plano: [`docs/nutricao/PLANO_NUTRICAO_2026-10-14.md`](../nutricao/PLANO_NUTRICAO_2026-10-14.md) §6.1, §10
(linhas "Perfil / Indicadores" e "Usuários") e §13 (L2a). Tabela regra → dono: §3.7, linha "Perfil
antropométrico do usuário". Primeiro consumidor em runtime do pacote `domain/nutrition/`.

**Dono.** `anthropometrics` ganhou `sex_code`/`activity_level_code` (o código canônico, ou `None`, a partir
de qualquer grafia que `normalize_sex`/`normalize_activity_level` aceitam) e os códigos de erro da validação
do perfil (`INVALID_SEX = "invalid_sex"`, `INVALID_ACTIVITY_LEVEL = "invalid_activity_level"`,
`INVALID_HEIGHT = "invalid_height_cm"`). A faixa de altura continua `HEIGHT_CM_RANGE`, os rótulos continuam
em `labels.py`. Nada mudou no bloco `nutrition` exportado: as telas já tinham `SEX_OPTIONS`,
`ACTIVITY_LEVEL_OPTIONS` e `NUTRITION_FORM_RANGES.height_cm` (o JSON gerado não muda).

**API.** `PUT /users/me` aceita `height_cm` e `activity_level` (além de `sex`); `PUT /users/{id}` (admin) os
herda pelo `UserUpdateIn` e `POST /users` valida `sex`. As três rotas usam os mesmos validadores de
`users.py`, que só chamam o dono: fora do vocabulário ou da faixa → 400
`{"error": <código do motor>, "message": ...}` (a mensagem interpola os códigos do enum e a faixa, nada
escrito à mão); tipo errado continua 422 do modelo. O banco grava SEMPRE o código canônico ('M' recebido
vira `male`). `None`/vazio não altera (`COALESCE`), como os demais campos do perfil. `height_cm` e
`activity_level` só entram no `SET` quando vêm preenchidos (`_anthropometric_set`), que é o mesmo efeito
do `COALESCE` com `None`. Assim, salvar o resto do perfil (fuso, unidade, Intervals) não depende da 074
aplicada (revisão de impacto em produção). Na leitura,
`_public_row` (`GET /users`, `GET/PUT /users/me`, `PUT /users/{id}`) e `/auth/me` devolvem `sex_code`/
`activity_level_code` do valor gravado: valor antigo sai canônico e desconhecido sai `null`, enquanto a 077
não tiver convertido a linha.

**Migração 077 (`077_users_sex_canonical.py`).** Backfill idempotente de `users.sex` para o vocabulário do
motor. Os apelidos ficam embutidos (cópia congelada de `_SEX_ALIASES` + os próprios códigos, já dobrados),
para o checksum não mudar quando o motor mudar. `test_migration_077_plano.py` confere a paridade nos dois
sentidos e a dobra (`_fold` = `fold_text`). Lê e atualiza com `COLLATE utf8mb4_0900_bin` porque a coluna é
`utf8mb4_0900_ai_ci`, para comparar o valor exato e o plano dizer exatamente o que falta. Um UPDATE por
código canônico com os valores encontrados no `IN`. NULL fica NULL, desconhecido fica e o `plan` loga o que
sobrou. Reexecução = 0 statements. `plan_down` é no-op documentado: o vocabulário canônico continua válido
para o código anterior. Não remodela forma conferida por migração anterior, por isso fica fora de
`RESHAPING_MIGRATIONS`. SQL conferido no MySQL 8.0 do DEV numa tabela temporária: 'M', 'm', 'MALE' e
'male ' agrupam separados e o UPDATE binário não toca 'male'.

**Telas.** `PerfilClient.tsx` ganhou a seção "Plano nutricional" (aviso curto, sem número; sexo, altura com
`min`/`max` de `NUTRITION_FORM_RANGES.height_cm` e nível de atividade), e `UsuariosClient.tsx` usa o mesmo
`SEX_OPTIONS`. As listas à mão (`sexOptions` e `users-admin-types.SEX_OPTIONS`, com 'M'/'F' e "Outro /
prefiro não informar") saíram. O rótulo passa a ser o do dono ("Outro"); deixar o campo vazio continua
sendo "não informar". O `PUT` só manda `height_cm`/`activity_level` quando preenchidos, e então um backend
anterior ao L2a (`extra="forbid"`) segue aceitando o salvamento sem eles.

**Guarda.** Dois padrões novos (§5), ambos com donos `anthropometrics.py` e `labels.py`, em py e ts:
- `vocabulario_sexo`: opção, comparação ou atribuição com código de sexo (`==`, `=`, `<>`, `IN`, `:` e
  campo opcional TS), e rótulo masculino/feminino. Na origin/develop ele achava 4 + 4 ocorrências nas
  duas telas. As duas foram corrigidas neste PR e a allowlist nasce vazia.
- `nivel_atividade_e_altura` (revisão do guardião): código de nível comparado ou atribuído ao campo
  `activity_level`, opção e rótulo sem homônimo comum ("Sedentário", "Muito ativo") e faixa de altura à mão
  (`height_cm` comparado com número, `HEIGHT_CM_RANGE` redefinido). Nasce vazio. "Leve", "Ativo" e
  `value: 'light'` sozinhos ficam de fora porque são genéricos no repo (tema, status, intensidade).

**Impacto em produção.** Medido em produção em 2026-10-15: `users.sex` com 'M' (1), 'F' (1) e NULL (2). A
077 faz 2 UPDATEs, 2 linhas, e preserva os 2 NULL. Na reexecução, 0 statements. Com `RUN_DB_MIGRATIONS=true`
a 077 roda antes das Lambdas. No intervalo, o código anterior lê `male`/`female` e a tela antiga mostra o
campo vazio. Ao salvar sem escolher, o `COALESCE` preserva o valor. A promoção exige o dry-run com 2
statements na 077. Gravar altura ou nível exige a 074 aplicada (gate ligado, como no L0). Sem ela, só essa
gravação falha, e o resto do perfil continua salvando. Backend e frontend sobem na mesma execução, o backend
antes. Num rollback, o frontend volta junto.

## 47. Nutrição — Lote L2b: plano nutricional (admin/PT) e metas diárias (2026-10-17)

Plano: [`docs/nutricao/PLANO_NUTRICAO_2026-10-14.md`](../nutricao/PLANO_NUTRICAO_2026-10-14.md) §5 (E1–E6, D4),
§6.2, §8.1, §10 e §15. Tabela regra → dono: §3.7 (linhas "Materialização", "Plano do aluno", "Sujeito do dia",
"Calendário de fases" e "Texto das limitações"). Primeiros consumidores em runtime da meta diária
(`compute_daily_target`); o L2a (§46) já consumia `anthropometrics` no perfil.

**Schema** (migrações 078 e 079; a 077 e a §46 são do L2a).
- `nutrition_plans` (078): vigência no banco, padrão 049/052/065: `current_user_id` gerada
  (`IF(status = 'published' AND superseded_at IS NULL, user_id, NULL)`) com UNIQUE, uma vigente por aluno;
  `effective_from` DATE (dia do aluno) e `superseded_at` DATETIME (relógio do banco). FK só para `users`;
  `created_by`/`published_by` lógicos.
- `nutrition_daily_targets` (079): `UNIQUE (user_id, local_date)`, `plan_id` lógico com índice, metas NULL quando
  o motor devolve None (nunca 0), `training_known` (a decisão do motor GRAVADA no cálculo) e `inputs_hash`
  (sha256 das entradas, só para não regravar o que não mudou).
- Desvios do rascunho do §6.2 (o §6.2 do plano foi atualizado com os nomes reais), todos para não converter nem
  decidir fora do dono: colunas de parâmetro com o nome do motor (`weekly_rate_share`, `surplus_share`,
  `fat_kcal_share`); `advanced` e `carb_min_g_per_kg` entram; `training_kcal_mode` guarda o `TdeeMode`
  (`explicit_training`/`included_in_factor`; na API o campo se chama `tdee_mode`); `diet_break_pattern` é o
  `BreakProtocol`. Na 079, `tdee_kcal` e `training_kcal` + `training_kcal_nature` (a base sem treino exigiria
  subtrair fora do dono e a kcal do treino é observada ou estimada, não planejada); `day_kind` é o
  `carb_cycling.DayKind` e o treino vira `is_training_day`/`training_known`; sem `data_quality` (o motor não define
  qualidade da meta); `water_ml` NULL até existir regra de água NO motor. Colunas do L8 nascem NULL.

**Motor** (`domain/nutrition`, `ENGINE_VERSION` inalterada: nenhuma conta existente mudou).
- `daily_target`: `TARGET_FREEZE_LAG_DAYS`, `is_target_frozen`, `first_recomputable_day`, `TargetWindow`,
  `WindowResult`, `week_window`, `publication_window`, `recompute_window`, `days_in_range`, `range_within_cap`,
  `days_to_materialize`, `ERR_DATE_FROM_FROZEN`/`ERR_DATE_TO_BEFORE_DATE_FROM`/`ERR_RANGE_TOO_LONG`,
  `SessionFacts`, `day_context_for`, `LIMIT_TRAINING_UNKNOWN_FUTURE_DAY` (+ `DayContext.training_known`),
  `SUBJECT_DATA_LIMITATIONS`, `missing_subject_data`, `TARGET_RECOMPUTE_MAX_DAYS`.
- `plan` (novo): `build_plan_settings` → `PlanSettings`, `STRATEGY_ONLY_FIELDS`, `PlanStatus`, `PlanState`,
  `plan_state`, `PublishedPlanRef`, `plan_in_force`, `GoalSummary`/`goal_summary`.
- `subject` (novo): `SubjectSeries.readings_on` (seleção POR GRANDEZA, `latest_valid`) → `SubjectReadings` →
  `resolve_subject` → `ResolvedSubject`; `NAVY_SITE_FIELDS` (`body_measurement_sites.HIP_SITE` nasceu aqui).
- `phases.resolve_stop_body_fat` (D-C: o do plano ou o default, e se veio do default).
- `bmr.DEFAULT_BMR_FORMULA`; `anthropometrics.WEIGHT_KG_RANGE` (20–350 kg, default de desenho, só o peso-alvo).
- `labels`: `PLAN_STATE_LABELS`, `PLAN_FIELD_LABELS`, `LIMITATION_LABELS`, `PREFIXED_LIMITATION_LABELS`,
  `NOTICE_LABELS`, `limitation_label`, `limitation_items` (todo `LIMIT_*`/`CLAMP_*`/`ERR_*` dos módulos da meta, do
  plano e das janelas tem texto; número no texto interpolado do dono).
- `export_for_frontend` (39 chaves): `plan_states`/`plan_state_labels`, `default_bmr_formula`,
  `strategy_only_fields`, `notices`, `form_ranges.weight_kg` e `form_ranges.<estratégia>.protein_basis`; espelho
  `nutrition-constants.ts` (`PLAN_STATE_OPTIONS`, `DEFAULT_BMR_FORMULA`, `strategyFormRanges`,
  `STRATEGY_ONLY_FIELDS`/`fieldAppliesToStrategy`, `NUTRITION_NOTICES`/`nutritionNotice`), paridade no vitest.

**Decisões de desenho.**
1. **Congelamento.** A meta de um dia congela quando `hoje >= dia + TARGET_FREEZE_LAG_DAYS` (2). Motivo: o sync do
   Intervals.icu preenche a kcal do treino na madrugada do dia seguinte (cron 05:45 BRT, `intervals_sync.py`); ontem
   ainda recebe a kcal observada, anteontem não muda mais. "Hoje" é o dia civil do ALUNO
   (`time_windows.user_today_for`, §12). Dia congelado nunca é escrito, nem inserido. O primeiro dia recalculável
   (`first_recomputable_day`, ontem) é o mesmo no recálculo do admin/PT e no `GET /me/plan`.
2. **Treino no `DayContext`.** Até hoje: há treino quando há `activities` com `session_status = 'done'` no dia; a kcal
   é `activities.calories_kcal` qualquer que seja a proveniência (dono da proveniência: `domain/calories_source.py`).
   Com mais de uma sessão, a kcal informada é a soma só se todas têm kcal; senão a natureza cai para MET com a soma
   das durações. Dia futuro: treino desconhecido, limitação `training_unknown_future_day`, e a decisão fica GRAVADA
   em `training_known` (revisão do #561: a leitura recalculava `dia <= hoje` e uma terça materializada na segunda,
   lida na quinta já congelada, aparecia como "sem treino" com o aviso de dia futuro). Ciclo de carbo e treinos
   planejados pelo Modelo ficam para o L8: `carb_cycling_enabled = true` é recusado com `carb_cycling_unavailable`.
3. **Fase.** Sem `nutrition_phase_events` (L8): `phase_kind` NULL.
4. **Faixas.** Na ENTRADA do plano a faixa da fonte bloqueia (400 com o código do motor); dentro do orquestrador ela
   continua só avisando (`out_of_range`), para um plano publicado não "quebrar" se a faixa mudar depois.
5. **Vigência por dia.** O plano de um dia é o publicado de maior `effective_from <= dia` (`plan_in_force`); o
   substituído continua valendo para os dias do seu período. Publicar grava `effective_from` = hoje do aluno. O
   `GET /me/plan` mostra o plano em vigor HOJE pelo mesmo `plan_in_force` (não por `superseded_at IS NULL`).
6. **Janelas no motor.** Publicação: `publication_window` cria as metas até o fim da semana e atualiza TODAS as já
   materializadas de hoje em diante (requisito: nenhuma meta futura não congelada fica com o plano antigo). Recálculo:
   `recompute_window` (a partir do primeiro dia recalculável; fim padrão no fim da semana; o intervalo pedido cabe em
   `TARGET_RECOMPUTE_MAX_DAYS`). O teto limita só a CRIAÇÃO; atualização de meta existente nunca é cortada
   (`days_to_materialize`). Semana: `week_window`.
7. **Concorrência.** Publicação otimista: a tela manda `expected_current_id` (a vigente que viu, ou null); a rota lê a
   vigente com `FOR UPDATE` no início e responde 409 `publish_conflict` (com `current_id`) se divergir; o UNIQUE da
   078 continua como defesa (corrida no UNIQUE também vira 409, com rollback). A materialização lê os planos com
   `FOR SHARE` na mesma transação: um `GET /me/plan` concorrente com uma publicação espera por ela (ou ela espera
   por ele), e nunca regrava uma meta com o plano antigo.
8. **Escrita enxuta.** `ensure_daily_targets` pula o upsert quando `plan_id`, `engine_version` e `inputs_hash` (sha256
   das entradas do cálculo: sujeito do dia, parâmetros do plano, contexto do dia) não mudaram, e grava o resto num
   `executemany` só. Conferido no código: `db_dataapi.Cursor.executemany` mapeia para `BatchExecuteStatement` com o
   `transactionId` da conexão (mesma transação); o `db_pymysql` reescreve num INSERT multi-linha.
9. **Sujeito por grandeza.** O serviço lê o histórico inteiro até o fim da janela (uma linha por dia na
   bioimpedância e por local na fita) e o motor escolhe, por grandeza, a leitura válida mais recente. O
   `ROW_NUMBER() OVER` das medidas saiu: a escolha deixou de ser do SQL.
10. **Preview.** Calcula os 7 dias da semana atual sem gravar. 422 só quando a meta de HOJE não é calculável, listando
    `missing_subject_data` com texto; dias antes da primeira pesagem aparecem sem meta, com as limitações.
11. **Acesso.** `nutrition_access.resolve_target_user`: próprio passa; terceiro exige `require_admin_or_pt`; PT sem
    vínculo formal (D-L), centralizado para o L11 trocar num lugar só. 404 do "sem plano" sempre com
    `no_published_plan` (a tela não trata 404 genérico como "sem plano").
12. **Decisões do §15 adotadas** com o padrão proposto: D-A, D-B (`clamped_by` aparece como "Ajustes do motor"), D-C
    (`resolve_stop_body_fat`/`goal_summary`; a tela mostra `goal_body_fat_is_default` da API), D-J (o TDEE adaptativo
    só sugere; não entra no L2b) e D-L.

**Guarda.** O `ESCOPO_NUTRICAO` já cobria `api/app/nutrition_*.py`, `frontend/src/app/(app)/nutricao/`,
`frontend/src/components/nutrition/` e `frontend/src/lib/nutrition-*`: nenhum glob novo. `api/app/nutrition_plans.py` e
`api/app/nutrition_targets_service.py` entraram em `MODULOS_DE_MOTOR` (`relogio_sem_fuso`). Nenhuma allowlist mudou;
0 ocorrência nos arquivos novos. Os desvios apontados pela revisão do guardião no #561 (janelas, "treino conhecido",
seleção do sujeito, %G de parada, textos e campos por estratégia na tela) não têm literal que a regex pegue; ficam
cobertos pelos testes dos donos e pelos testes de rota.

**Riscos para os próximos lotes.**
- **Metas dependem de alguém abrir a tela.** Não há job: as metas de um dia só são (re)calculadas enquanto ele não
  congelou E alguém chama a materialização (publicação, recálculo, `GET /me/plan`; o diário do L4). Dia que
  congela sem ninguém abrir fica sem meta (dia sem linha) ou com a meta calculada antes da kcal do Intervals chegar.
  O L4 decide, NO motor, se o "materializa se faltar" do `GET /nutrition/days/{date}` insere uma vez um dia congelado
  sem linha e se a tela do diário garante a visita diária. Registrado também no §15 do plano.
- **L8.** Com os treinos planejados pelo Modelo, `training_unknown_future_day` deixa de aparecer; o ciclo de carbo
  precisa do risco já registrado no §45 (ciclar sobre a média semanal das metas-base no modo explícito).
- **L2a.** `normalize_sex` aceita `M`/`F` e o vocabulário do motor: o backfill de `users.sex` da 077 não muda nenhuma
  meta. `subject.py` usa `normalize_sex`/`normalize_activity_level` (o `Subject` precisa do enum);
  `sex_code`/`activity_level_code` do L2a são para gravar/devolver o código no perfil.

**Impacto em produção.** Duas tabelas novas (078, 079), um router novo sob o `{proxy+}` da api-core
(`/nutrition/*`) e um item de menu. Nenhuma linha existente é alterada e, sem plano publicado, nada é
materializado (nenhum aluno tem plano em produção hoje). A promoção exige `RUN_DB_MIGRATIONS=true` e um dry-run
listando os 2 UPDATEs da 077 (L2a, #560), 1 `CREATE TABLE` da 078 e 1 da 079, com 0 statements na reexecução. Antes
das migrações as rotas de nutrição respondem 503 citando 078/079. Smoke pós-promoção: `GET /nutrition/me/plan`
aceitando só 200 ou 404 com `no_published_plan`. Sem mudança de infra. Risco operacional: as metas dependem de
alguém abrir a tela (acima).

## 48. Nutrição — Lote L3a: catálogo de alimentos com a TACO 4ª ed. (2026-10-17)

Plano: [`docs/nutricao/PLANO_NUTRICAO_2026-10-14.md`](../nutricao/PLANO_NUTRICAO_2026-10-14.md) §4, §6.2, §7,
§8.1, §10, §13 e §15 (D-F, D-G, R1, R4, R5, R6). Tabela regra → dono: §3.7 (linhas "Texto de alimento",
"Catálogo de alimentos", "Matemática da refeição" e "Rótulos pt-BR"). Fonte, regras de conversão e conferência:
[`docs/nutricao/fontes/README.md`](../nutricao/fontes/README.md). USDA e Open Food Facts ficam para o L3b.

**Schema** (migrações 080 e 081).
- `food_items` (080): valores POR 100 g/ml com os códigos da RDC 429 do motor (`energy_kcal`, `carbohydrates_g`,
  `total_sugars_g`, `added_sugars_g`, `protein_g`, `total_fat_g`, `saturated_fat_g`, `trans_fat_g`, `fiber_g`,
  `sodium_mg`; NULL = a fonte não informa), porção do rótulo (`serving_size`, `household_measure`,
  `servings_per_package`), `source`/`source_id`/`source_version` com `UNIQUE (source, source_id)`, `barcode` com
  índice, `name_normalized` com FULLTEXT (criado no CREATE, antes do seed: R1) e índice de prefixo (64),
  `license`/`citation` por linha (R4), `owner_user_id` (NULL = público; FK `users` ON DELETE CASCADE),
  `label_s3_key` (L6), `is_active`, `created_by` lógico. `food_portions` (080): `food_id` lógico, `amount` NA BASE
  do alimento, `owner_user_id` (NULL = pública; FK `users`).
- Desvios do rascunho do §6.2 (o plano registra o ajuste): colunas com os códigos da RDC 429 do motor no lugar de
  `carbs_g`/`fat_g`/`sugars_total_g`/`serving_size_g`/`serving_label` (sem conversão de nome fora do dono, como na
  078); porção em `amount` (na base g ou ml), não `grams`; `food_portions.owner_user_id` (porção do usuário num
  alimento público fica privada, D-G).
- Seed (081): 597 linhas públicas da TACO 4ª ed., embutidas no módulo (o checksum do runner cobre os dados, D-F/R6),
  geradas por `api/scripts/gerar_seed_taco.py` a partir de `docs/nutricao/fontes/taco_4a_edicao.xlsx` (SHA-256 no
  bloco e no README). `INSERT ... ON DUPLICATE KEY UPDATE` em blocos de 50 (12 statements; maior SQL traduzido pelo
  shim 8.343 bytes, limite de 65.536; maior payload estimado 78.988 bytes); na reexecução lê as linhas `taco` e,
  com contagem 597 e checksum iguais, 0 statements; linha divergente regrava só o bloco dela. `name_normalized`,
  citação, licença e versão embutidos são conferidos com o motor por teste (`test_migration_081_plano.py`).

**Motor.**
- `food_text` calibrado contra os 597 nomes reais (49 buscas em `test_nutrition_food_text_taco.py`). Medição antes
  → depois: acerto no topo com nota de candidato 33 → 47 de 49 (as 2 restantes, "frango grelhado" e "frango com pele
  assado", são ambíguas e ficam entre os 3 primeiros sem pré-seleção); nota ≥ 0,6 do item certo 37 → 49; pré-seleção
  errada 1 → 0 ("peito de frango sem pele cru" pré-selecionava "com pele"). O caso do §45 ("arroz branco cozido" ×
  "Arroz, tipo 1, cozido") foi de 0,57 para 1,0. Mudanças: "sem" deixou de ser stopword; sinônimos pt-BR novos
  (qualificador implícito: leite de vaca, ovo de galinha inteiro, carne bovina, pão de trigo; variedade: arroz branco
  = tipo 1, mussarela = mozarela, castanha-do-pará = castanha-do-brasil; gênero e número do preparo: cozida/crua/
  assada/grelhada/frita/refogada/moída); `NOISE_TOKENS` (UHT, "cozido/10minutos"); a troca de sinônimos é numa
  passada só (teste: nenhuma forma canônica contém outra variante; normalização idempotente). Constantes novas,
  defaults de desenho calibrados na TACO: `QUERY_COVERAGE_WEIGHT` = 0,3 (testados 0; 0,2; 0,25; 0,3; 0,35; 0,4;
  0,5. Só com a normalização nova e sem cobertura: 41 de 49 (42 com nota de candidato); com 0,2: 46; de 0,25 a 0,5: 47, todos com 49/49 candidatos e 0 pré-seleção errada.
  Escolhido 0,3, perto do limite inferior, para a cobertura não dominar a nota: de 0,4 em diante as pré-seleções
  sobem de 25 para 32–35, mais risco numa busca parcial fora do conjunto) e `MATCH_AUTO_ACCEPT_MIN_MARGIN` = 0,05 (só
  o PRIMEIRO candidato é pré-selecionado e com folga sobre o segundo). `MATCH_MIN_SCORE` (0,6), `MATCH_AUTO_ACCEPT_SCORE`
  (0,85) e `TOKEN_JACCARD_WEIGHT` (0,5) não mudaram. Busca: `search_terms` (termos ≥ `FULLTEXT_MIN_TOKEN_CHARS` = 3,
  o `innodb_ft_min_token_size` padrão), `SEARCH_CANDIDATE_POOL` = 60, `order_for_search` (todos os candidatos do
  FULLTEXT pela mesma nota; o limiar de candidato não corta a tela) e `search_limit` (20, até 50).
- `catalog` (módulo novo; o vocabulário do catálogo não tinha dono): `FoodSource`, `READ_ONLY_SOURCES` (TACO),
  `TACO_CITATION`/`TACO_LICENSE`/`TACO_SOURCE_VERSION`, `FOOD_BASES` (códigos de `meal_math.Unit`),
  `PER_100_FIELDS`/`SERVING_FIELDS`/`REQUIRED_PER_100_FIELDS` (códigos da RDC 429), `validate_food_values` (erros:
  número inválido ou negativo, obrigatório ausente, componente acima da massa da base — 100 g; 100 ml × 2 g/ml,
  `ML_BASIS_MAX_DENSITY_G_PER_ML`, default de desenho —, energia acima de 9 kcal por grama da base; avisos:
  Atwater fora de `meal_math.ATWATER_TOLERANCE_SHARE`, subtotal acima do total, macros acima da base) e
  `validate_portion_amount` (`PORTION_AMOUNT_MAX` = 2.000, default de desenho).
- `labels`: `FOOD_SOURCE_LABELS`, `FOOD_BASIS_LABELS`, `RDC429_FIELD_LABELS` e o texto de cada código do catálogo
  (número interpolado do dono). `export_for_frontend` (48 chaves): `food_sources`/`food_source_labels`,
  `food_bases`/`food_basis_labels`, `default_food_basis`, `food_per_100_fields`, `food_serving_fields`,
  `food_required_fields`, `food_citations`; espelho `nutrition-constants.ts` (`FOOD_*`), paridade no vitest.
- `ENGINE_VERSION` inalterada: nenhuma conta de meta mudou.

**Consumidores** (só consomem). `api/app/nutrition_foods.py`: busca (FULLTEXT booleano `termo*` sobre o nome
normalizado, prefixo `LIKE` quando o termo é curto demais para o índice, lote do motor, ordem e pré-seleção do motor,
citação por item), detalhe com porções, cadastro e edição (400 com os códigos do motor; avisos em `warnings`),
porções e promoção. `api/app/nutrition_access.py`: `can_view_food`, `can_use_food`, `can_edit_food` (TACO nunca;
privado só o dono; público só o admin), `can_add_portion`, `portion_owner_for`, `can_promote_food`,
`can_list_private_foods`, `ownership` (`is_public`/`is_own`) e as condições SQL de visibilidade (`foods_visibility`;
`portions_visibility`: públicas + as do usuário + as do dono num alimento privado, para o admin ver o que a promoção
leva junto). A rota só monta o SQL com a expressão do motor (`fulltext_boolean_query`/`prefix_pattern`) e a condição
do acesso. 503 citando a
080/081 (`nutrition_schema.require_food_tables`). Tela `/nutricao/alimentos` (`AlimentosClient`, `FoodSearch`,
`FoodForm`, `FoodDetailCard`, `PortionsEditor`, `FoodCitationFooter`, `NutritionSubnav`): campos, rótulos, unidades,
obrigatórios, fontes e citações do JSON gerado; nota, ordem e permissões da API.

**Decisões.**
1. **D-G**: o cadastro do usuário nasce privado; o admin promove (o alimento e as porções do dono viram públicos).
   O admin vê os privados de todos só na busca `scope=private_all`; a busca normal de qualquer um mostra públicos +
   os próprios.
2. **TACO somente leitura** pela API, inclusive para o admin (409 `food_source_read_only`): é tabela publicada,
   citada por linha; valor diferente vira alimento novo. Porção numa linha da TACO é permitida (privada do usuário;
   pública quando é o admin).
3. **NULL ≠ 0**: `NA` (não aplicável), `*` (em reavaliação) e vazio (não analisado) da TACO viram NULL no seed;
   só `Tr` (traço) vira 0. O diário (L4) decide, NO motor, como somar um item com macro NULL (ex.: proteína dos 8
   óleos, NA). Desde o M85 (§48.1) os `*` com equivalente no USDA FoodData Central são preenchidos pela 084; `NA`
   e vazio continuam NULL.
4. **Grupos**: a planilha junta "Nozes e sementes" a "Leguminosas"; o seed segue a publicação (15 grupos).

**Guarda.** Padrões novos `catalogo_alimentos` (constante do catálogo redefinida com número fora de `food_text`/
`catalog`, e `SYNONYMS`/`STOPWORDS`/`NOISE_TOKENS` redefinidos; nasce vazia) e `nota_matching` (nota do matching
comparada com número no escopo de nutrição; proibição pura; nasce vazia). Os arquivos novos já estavam no `ESCOPO_NUTRICAO` (`api/app/nutrition_*.py`,
`domain/nutrition/`, `(app)/nutricao/`, `components/nutrition/`, `lib/nutrition-*`), conferidos por
`test_arquivos_do_catalogo_de_alimentos_estao_no_escopo_de_nutricao`. Nenhuma allowlist cresceu.

**Riscos para os próximos lotes.**
- **Mudar a normalização depois da 081 aplicada** exige migração nova que regrave `name_normalized` (TACO e
  cadastros). A 081 fica congelada (`CHECKSUM_CONGELADO` em `test_migration_081_plano.py`; o gerador só regrava com
  `--migracao-nao-aplicada`): a paridade nome normalizado/citação × motor vale para a migração MAIS RECENTE que
  grava esses dados e passa para a migração nova quando ela existir (a 081 fica só com a conferência dos dados que
  gravou).
- **L4**: itens com macro NULL (óleos sem proteína/carbo; itens em reavaliação sem energia; 2 sais sem energia)
  precisam de regra no motor antes de somar a refeição (feito no L4, §52; os itens em reavaliação com equivalente
  no USDA foram preenchidos pelo M85, §48.1).
- **Data API e FULLTEXT**: o termo da busca vai como parâmetro dentro de `AGAINST`; o dublê da Data API e o MySQL
  8 do CI cobrem, mas a primeira busca em produção deve ser conferida no smoke.

**Impacto em produção.** Duas tabelas novas (080: 2 `CREATE TABLE`) e a carga de 597 linhas públicas da TACO (081:
12 statements de até 50 linhas; 0 na reexecução), um router novo sob o `{proxy+}` da api-core
(`/nutrition/foods/*`) e uma página estática (`/nutricao/alimentos`, sem item de menu novo). Nenhuma linha existente
é alterada. Antes das migrações as rotas respondem 503 citando 080/081. A promoção exige `RUN_DB_MIGRATIONS=true` e
um dry-run que lista, deste lote, 2 CREATE (080) + 12 INSERT (081) — além das migrações anteriores ainda pendentes
na `main` (074–079, se a promoção levar o L0–L2b junto). Tamanho de statement: estimativa do `_translate_sql` do shim
(maior SQL 8.343 bytes, limite 65.536); a escrita dos 12 blocos pelo shim é exercitada na integração contra o dublê
da Data API (`test_081_grava_os_12_blocos_pelo_shim_da_data_api`), não na Data API real. Busca medida no MySQL 8.0.46
do DEV com PyMySQL (schema descartável `_test`, conexão nova por requisição, 240 requisições de 12 buscas): p50 91 ms,
p95 130 ms, máx 211 ms (meta: 300 ms); a nota do motor sobre 60 candidatos custa ~4,5 ms. Na Data API cada busca faz
3–4 chamadas (transação + SELECT): medir no smoke pós-promoção. Depois do migrate em produção: `SELECT COUNT(*) FROM
food_items WHERE source = 'taco'` = 597; smoke `GET /nutrition/foods/search?q=arroz` (FULLTEXT com parâmetro no
`AGAINST`, primeiro uso em produção) e `?q=ov` (prefixo), ambos 200. Sem mudança de infra.

### 48.1 M85 — campos "em reavaliação" da TACO com o USDA FoodData Central (migração 084, 2026-10-18)

Decisões do usuário: para os itens da TACO em reavaliação (`*`), usar o valor do USDA FoodData Central (2026-10-17);
o iogurte sabor abacaxi fica SEM valor, como o coco verde (2026-10-18). Fonte e conferência:
[`docs/nutricao/fontes/README.md`](../nutricao/fontes/README.md) (seção USDA). Sem dono novo: o formato da
proveniência é do `catalog` (§3.7) e o texto da nota de `labels`; a §53 não foi usada.

**Levantamento** (planilha da TACO pelo gerador da 081, `campos_marcados(MARCADOR_REAVALIACAO)`): 10 itens com `*`
nos 6 campos que o catálogo e o diário usam (energia, carboidrato, proteína, gordura, fibra, sódio).
- Preenchidos (2 itens, 8 campos; SR Legacy, publicação 2019-04-01, consulta 2026-10-18): 457 "Leite, de vaca,
  desnatado, UHT" (energia + 3 macros) ← fdcId 173432 "Milk, nonfat, fluid, without added vitamin A and vitamin D";
  458 "Leite, de vaca, integral" (energia + 3 macros) ← 172217 "Milk, whole, 3.25% milkfat, without added vitamin A
  and vitamin D". O sódio dos leites é medido pela TACO e fica; a fibra deles é `NA` e fica NULL.
- Continuam NULL, com o motivo em `SEM_EQUIVALENTE` do gerador: 450 "Iogurte, sabor abacaxi" (decisão do usuário: o
  iogurte de frutas mais próximo do FDC, fdcId 170889, ~99 kcal, é outro produto; os iogurtes de sabor da TACO têm
  ~68–70 kcal), 591 "Coco, verde, cru" (o FDC só tem a polpa madura, que a TACO já tem como 590, e a água de coco,
  TACO 478) e a fibra `*` de 6 preparações cárneas (280 bacalhau refogado; 330/331 almôndegas; 415/416/417
  hambúrguer industrial — o FDC só tem as versões de carne pura ou um produto americano de outra formulação). A
  fibra não entra no dia registrado (`intake_macros`).
- Fora por definição: os 2 sais sem energia (516, 517) são `NA`, não `*`.

**Schema** (084). `food_items.field_sources_json JSON NULL` (nova; NULL = todos os valores são da fonte da linha) e
`UPDATE` só dos campos `*` das 2 linhas, com os valores literais do snapshot versionado
(`docs/nutricao/fontes/usda_taco_reavaliacao.json`, SHA-256 no bloco gerado) mapeados pelo motor do L3b
(`external_sources.map_usda_food`: ids de nutriente, unidade, energia 1008, base 100 g) e arredondados para 2 casas
pela regra da 081 (`gerar_seed_taco.converter_valor`). Proveniência por linha: campos, fonte `usda`, fdcId, versão
(`sr_legacy-2019-04-01`), nome no FDC, data da consulta, motivo `taco_under_review`, citação do FDC com o tipo e o
FDC ID e licença CC0. Banco com a 081: 1 `ALTER` + 2 `UPDATE` = 3 statements; 0 na reexecução; valor diferente e não
nulo num campo a preencher aborta antes do 1º statement (nunca sobrescreve medido); `down` devolve NULL e faz `DROP
COLUMN`. Gerador: `api/scripts/gerar_taco_usda_reavaliacao.py` (`--baixar` refaz o snapshot pelo cliente do L3b;
`--verificar`; `--gravar` só com `--migracao-nao-aplicada`; a 084 fica congelada em `test_migration_084_plano.py`).
A 081 continua congelada (checksum e SHA-256 da planilha iguais): o `plan` dela, que compara com o próprio seed, passa
a ver o bloco dos itens preenchidos (451–500) como divergente (o runner nunca a reexecuta; a integração confere
exatamente esse bloco).

**Motor.** `catalog`: `FieldSourceReason`, `FieldSource`, `parse_field_sources` (texto/bytes/lista; malformado ou
campo desconhecido → `()`; um campo, uma origem), `field_sources_json` (forma canônica gravada) e
`field_sources_snapshot` (forma do snapshot do diário). `labels`: `FIELD_SOURCE_REASON_LABELS` (rótulo da TACO
interpolado de `FOOD_SOURCE_LABELS`), `field_source_note` ("Valor energético, carboidratos, proteínas e gorduras
totais: USDA FoodData Central (em reavaliação na TACO 4ª ed.)."), `field_source_detail` (nome no FDC, citação,
licença e data da consulta, para o tooltip) e `field_sources_public`. O gerador passa a linha final (o que a TACO
mediu + o preenchido) por `catalog.validate_food_values`: erro falha alto; os 2 leites passam sem aviso de Atwater
(integral 61 × 61,15 kcal dos macros; desnatado 35 × 34,54). `meal_math`/`weekly_budget` NÃO mudaram: a regra do
diário é a mesma, os dados é que ficaram completos. `ENGINE_VERSION` inalterada.

**Consumidores.** `nutrition_foods.food_public` (busca, detalhe, cadastro, promoção e as respostas do L3b) devolve
`field_sources` com a nota; `nutrition_meals.food_snapshot` congela a origem no item (E4) e o item público devolve
`field_sources` (snapshot anterior: `[]`). `nutrition_schema.require_food_tables` responde 503
`food_catalog_migration_pending` citando a 084 quando a coluna falta (toda leitura de `food_items` a seleciona;
cache só do positivo). Tela: `FieldSourcesNote` (nota discreta no `FoodDetailCard`; ícone com a nota no item da
refeição, `MealCard`; no tooltip da fonte na busca), texto pronto da API, sem regra numérica.

**Medição** (seed da 081 × 081 + 084, motor do L4 nas 597 linhas): itens sem energia 6 → 4 (iogurte sabor abacaxi,
coco verde e os 2 sais); itens com energia + 3 macros conhecidos (dia registrável) 590 → 592; NULL por campo:
energia 6 → 4, carboidrato e proteína 15 → 13, gordura 7 → 5, fibra 235 e sódio 10 sem mudança. Os 2 leites passam a
ter só a fibra ausente (`NA`, carboidrato > 0: continua desconhecida pela regra do L4), então "só fibra ausente" vai
de 220 para 222 e "fibra desconhecida" de 72 para 74. Exemplo: 200 g de leite integral passam de "energia
desconhecida" a 122 kcal (6,30 g de proteína, 9,56 g de carboidrato, 6,54 g de gordura); um café com leite (100 g de
café + 200 g de leite) passa a contar como dia registrado (131,07 kcal), entra no banco e na aderência; o total segue
com a fibra incompleta.

**Guarda.** Nenhum padrão novo e nenhuma allowlist mudou: o arquivo novo do FE (`FieldSourcesNote.tsx`) está no
`ESCOPO_NUTRICAO` (`test_arquivos_do_catalogo_de_alimentos_estao_no_escopo_de_nutricao`); o gerador e a migração
consomem o motor (`map_usda_food`, `FieldSource`, `field_sources_json`) e a guarda não varre `api/scripts/` nem
migrações. Sem padrão para o literal `taco_under_review` (sugestão opcional do guardião): é código estável, não
número, e a tela não o usa (lê `note`/`detail` prontos).

**Impacto em produção.** A promoção #571 (17/10/2026, 23h10 UTC, deploy 39077773130) aplicou 074–083 em produção (conferência do Tech Lead, só leitura:
`food_items` com 597 linhas `taco`, `meal_items` existe). A próxima promoção leva SÓ a 084: 3 statements (1 `ALTER`
+ 2 `UPDATE`) e um novo dry-run lista 0. O dry-run em produção já lê as linhas reais da TACO (a trava de valor medido
roda nele também). Linhas que mudam: 2 de `food_items` (`taco` 457 e 458), 8 campos, mais a coluna nova (NULL nas
demais). O `ALTER ... AFTER citation` não é INSTANT (`food_items` tem FULLTEXT): reconstrói a tabela (~600 linhas
mais os cadastros), em milissegundos a poucos segundos, com lock curto de metadados — a nutrição já está no ar, então
uma requisição ao catálogo pode esperar esse instante. Registros do diário NÃO são reescritos: `meal_items` congela
nutrientes e snapshot (E4); a nutrição foi publicada horas antes, então `meal_items` deve estar vazio ou quase (a
conferência é do Tech Lead). Item de leite gravado antes da 084 continua com energia e macros desconhecidos e o dia
dele incompleto; editar a refeição mantendo o item reusa o snapshot congelado (decisão 4 do L4): para usar o valor
novo, é preciso tirar o item e incluí-lo de novo. Depois do migrate: `SELECT COUNT(*) FROM food_items WHERE
field_sources_json IS NOT NULL` = 2, `SELECT COUNT(*) FROM food_items WHERE source = 'taco' AND energy_kcal IS NULL`
= 4 e o leite integral (`source_id` 458) com 61 kcal; smoke `GET /nutrition/foods/search?q=leite integral` com
`field_sources[0].source_id` = 172217. Janela de deploy: a promoção PRECISA de `RUN_DB_MIGRATIONS=true` (o pipeline
faz migrate → Lambdas → frontend): com o código novo no ar antes da 084, o catálogo e o diário — já usados em
produção — respondem 503 citando a 084 (nunca 500; o cache guarda só o positivo, e o 503 some sozinho depois da
migração). Entre o migrate e a troca das Lambdas, o código antigo lê os leites já preenchidos, sem a nota (ignora a
coluna nova). Se a 084 falhar, o job para antes das Lambdas e a reexecução converge. Reversão: o `down` da 084 não é
acionável pela Lambda (`status`/`dry-run`/`migrate`/`replay-progression`). O caminho real é (1) reverter o código —
seguro, o código antigo não lê a coluna, mas os 2 valores do USDA ficam sob a citação da TACO e sem a nota — e (2)
migração nova (próximo número livre) com a lógica do `plan_down` (NULL só se os valores ainda forem os da 084,
depois `DROP COLUMN`). Na ordem inversa (coluna derrubada com o código do M85 no ar) catálogo e diário ficam em 503.
Refeições gravadas no intervalo mantêm nutrientes e origem congelados (E4). Sem mudança de infra.

## 49. Nutrição — Lote L3b: USDA FoodData Central e Open Food Facts no catálogo (2026-10-17)

Plano: [`docs/nutricao/PLANO_NUTRICAO_2026-10-14.md`](../nutricao/PLANO_NUTRICAO_2026-10-14.md) §4, §8.1, §10,
§12, §13 e §15 (R4, R7). Tabela regra → dono: §3.7 (linhas "Fontes externas do catálogo" e "Catálogo de alimentos").
Infra (secret `musclefit/usda-api-key`, `USDA_API_KEY_SECRET_ARN` só na api-core, `secrets_bootstrap`) veio no #562.

**Sem migração.** Grava em `food_items` (080): `source='usda'` (`source_id` = fdcId, `source_version` =
`foundation-AAAA-MM-DD`/`sr_legacy-AAAA-MM-DD`, citação com o tipo e o FDC ID) e `source='off'` (`source_id` =
`barcode` = GTIN canônico, `source_version` = data da última modificação do produto no OFF, citação ODbL com o link
do produto). As duas nascem PÚBLICAS (`owner_user_id` NULL: dado da fonte, igual para todos), com `created_by` =
quem consultou/importou, e são somente leitura (409 no PUT). Idempotência pela chave única `(source, source_id)`:
`INSERT ... ON DUPLICATE KEY UPDATE id = id` + releitura pela chave (duas consultas simultâneas gravam uma linha).

**Motor.**
- `external_sources` (módulo novo; o mapeamento das fontes não tinha dono): USDA só Foundation e SR Legacy
  (`USDA_DATA_TYPES`), ids do FDC com a unidade esperada (`USDA_NUTRIENT_IDS`: 1003, 1004, 1005, 1079, 1093 em mg,
  2000, 1258, 1257 — só ids vistos nos payloads reais; açúcares adicionados não existem nesses tipos e ficam None),
  energia `USDA_ENERGY_CANDIDATES` 1008 > 2048 (Atwater específico) > 2047 (geral) > 1062 (kJ → kcal por
  `meal_math.kj_to_kcal`); OFF pela API v3.6 lendo `nutrition.aggregated_set` (só `preparation` = `as_sold` e `per`
  = 100g/100ml → base g/ml), chaves `OFF_NUTRIENT_KEYS`, sódio g → mg (`catalog.MG_PER_G`), valor de origem
  `estimate` ignorado (`OFF_IGNORED_NUTRIENT_SOURCES`), energia `energy-kcal` > `energy-kj` > `energy` (kJ) e o
  `value_computed` do OFF nunca; nome em português primeiro (`OFF_NAME_FIELDS`), porção do rótulo se passar na regra
  da porção; `OFF_PRODUCT_FIELDS` (o que o cliente pede). GTIN-8/12/13/14 com dígito verificador GS1
  (`GS1_CHECK_WEIGHTS`, `GS1_MODULUS`) e forma canônica (UPC-A ganha 0; GTIN-14 com 0 perde o 0: `normalize_gtin`).
  `OFF_LOOKUPS_PER_USER_DAY` = 30 (plano §8.1), `off_lookup_allowed` e o critério do que conta
  (`OFF_LOOKUP_COUNTED_SOURCE`: linha `off` criada pelo usuário no dia dele); busca do USDA com o corpo e a resposta
  montados aqui (`usda_search_request`: termo, tipos aceitos, `USDA_SEARCH_PAGE_SIZE` = 25, default de desenho;
  `usda_search_result`: `total_hits` + itens), para a rota e o cliente não lerem nem montarem o payload.
  `EnergyOrigin` diz de onde veio a energia.
- `catalog`: citação e licença do USDA (citação sugerida pelo FDC; CC0) e do OFF (aviso de atribuição da ODbL, seção
  4.3; ODbL/DbCL) em `SOURCE_CITATIONS`/`SOURCE_LICENSES`; `READ_ONLY_SOURCES` = TACO, USDA e OFF;
  `EXTERNAL_REQUIRED_PER_100_FIELDS` = só a energia (a fonte pode não informar um macro: NULL, nunca 0);
  `validate_food_values(..., required=)` (o default segue o do cadastro; o Atwater só roda com os quatro).
- `labels`: `USDA_DATA_TYPE_LABELS` ("Foundation Foods", "SR Legacy (Standard Reference)"), textos de `invalid_barcode` (comprimentos interpolados de `GTIN_LENGTHS`),
  `off_daily_limit` (limite interpolado) e `usda_data_type_not_supported`; o texto do somente leitura passou a cobrir
  base externa. `export_for_frontend` (51 chaves): + `food_licenses`, `usda_data_types`, `usda_data_type_labels`;
  `food_citations` ganhou `usda` e `off`.
- `ENGINE_VERSION` inalterada: nenhuma conta de meta mudou.

**Payloads reais** (`api/tests/unit/data/nutrition_external/`, gravados em 2026-10-17 dentro do container `api` do
DEV pelo próprio cliente, sem a chave). O que eles mostraram e virou regra: os Foundation de 2024 não têm 1008, só
2047/2048 (fdcId 2710825); a v3.6 do OFF não devolve mais `nutriments`/`*_100g` (o pedido com `fields=nutriments`
voltou sem a chave), o conjunto é `nutrition.aggregated_set`; o OFF guarda açúcares adicionados ESTIMADOS a partir
dos ingredientes (`source: estimate`, `modifier: ~`); o `value_computed` do agregado veio na escala da porção (64
kcal) ao lado do valor por 100 g (325 kcal) — mais um motivo para nunca usá-lo.

**Consumidores** (só consomem). `api/app/nutrition_external_foods.py` (api-core, `{proxy+}`): `GET
/nutrition/foods/barcode/{ean}`, `GET /nutrition/foods/usda/search?q=` e `POST /nutrition/foods/usda/{fdc_id}/import`
— monta o SQL, conta as linhas `off` do usuário desde o início do dia DELE (`time_windows.user_day_start_db`; o
módulo entrou em `MODULOS_DE_MOTOR`), compara pelo `off_lookup_allowed`, chama o cliente e devolve o que o motor
decidiu (422 `invalid_external_food` com os códigos rotulados; avisos em `warnings`). A conexão com o banco nunca
fica aberta durante a chamada externa. `api/app/external_food_sources.py`: só transporte (`urllib` com um opener que
NÃO segue redirecionamento — o 3xx levaria o `X-Api-Key` para outro host —, timeout `EXTERNAL_HTTP_TIMEOUT_SECONDS` =
8 s, chave do USDA no cabeçalho `X-Api-Key` e recusada como "não configurada" se tiver espaço ou caractere de controle,
User-Agent `MuscleFit/1.0 (+https://musclefit.com.br)` sem e-mail, corpo até 5 MB, falhas — inclusive
`http.client.HTTPException` como `IncompleteRead` — → 404/502/503/504 com código `<fonte>_<tipo>`, nunca 500; log
sem URL nem chave). Tela `/nutricao/alimentos`: `BarcodeLookup` (EAN digitado), `UsdaSearch` (aviso de busca em
inglês, botão importar), citação e licença por item (`FoodDetailCard`) e no rodapé (`FoodCitationFooter`, com
`food_licenses`); nenhum número ou rótulo de domínio escrito à mão.

**Decisões.**
1. Item importado é PÚBLICO e somente leitura (USDA e OFF): é dado oficial/aberto, citado por linha; valor diferente
   vira alimento novo do usuário.
2. Na importação só a energia é obrigatória; erro do catálogo (negativo, acima da base) recusa com 422 e não grava;
   Atwater e subtotal viram aviso.
3. Busca do USDA sem limite por usuário (a cota é a da chave, 1.000 req/h): a tela só busca no envio, não a cada
   tecla. Sem chave: 503 `usda_not_configured` em todas as rotas do USDA, antes de tocar no banco.
4. O cache do código de barras é a própria linha `off` (chave `source`/`source_id`); cache achado não chama o OFF
   nem conta no limite. Leitor pela câmera e porções do USDA (`foodPortions`) ficam fora.

**Limitações registradas.**
- O limite diário conta só o que ficou no banco: consulta sem resultado no OFF (404), recusada pelo motor (422) ou com
  erro externo NÃO conta, então um usuário pode gastar consultas ao OFF sem limite com códigos inexistentes. Duas
  consultas simultâneas do mesmo usuário podem passar do limite em uma.
- A contagem usa o prefixo `source` da chave única e filtra `created_by`/`created_at` (não há índice próprio): barato
  enquanto o cache `off` tiver poucos milhares de linhas; um índice `(created_by, created_at)` exigiria migração.
- O timeout de 8 s vale por operação de rede (conexão e cada leitura; o DNS não tem limite); uma resposta que pinga
  devagar pode passar disso. Somado ao Aurora acordando do auto-pause (até ~20 s de espera no `db_dataapi`) e ao cold
  start, a requisição pode passar dos 29 s do API Gateway (504 sem corpo JSON). Improvável (a tela já acordou o banco);
  um prazo total por requisição fica como melhoria.
- Busca do USDA sem limite por usuário: um laço de um cliente pode esgotar a cota da chave (1.000 req/h, a mesma para
  todos) e deixar todos com 503 `usda_unavailable` por até 1 h; o OFF pode estrangular o User-Agent do app. Com a base
  atual de usuários o risco é baixo; registrar as tentativas exigiria tabela (migração). Usar no `.env` do DEV uma chave
  diferente da de produção evita que o DEV gaste a cota de produção.
- O nome do USDA é em inglês; o `name_normalized` passa pelos sinônimos do `food_text` (ex.: "rice" → "arroz"), então
  parte dos itens importados aparece também na busca em português.

**Guarda.** Padrões novos, os dois nascem vazios (tabela do §5):
- `fontes_externas_alimentos` (dono `external_sources`, escopo de nutrição): id de nutriente do USDA, chave de
  nutriente do OFF (inclusive `*_100g`), literal de tipo/preparo (`"Foundation"`, `"SR Legacy"`, `"as_sold"`),
  estrutura crua dos payloads (`foodNutrients`, `nutrientId`, `aggregated_set`, `nutriments`, `value_computed`,
  `fdcId`, `dataType`, `totalHits`), constante do dono redefinida e EAN validado à mão (`\d{13}`, `% 10`). Os ids
  nunca seguidos de casa decimal (`2000.0` da porção máxima) e sem o 2000 (genérico demais). Sem padrão, por serem
  genéricas: as chaves `energy`, `fat`, `sodium`, `proteins`, `sugars`, `fiber`, `carbohydrates` do OFF (cobertas
  pela leitura da estrutura crua).
- `kj_kcal` (dono `meal_math`, repo inteiro): 4,184 ou `KJ_PER_KCAL` redefinido fora do dono.

Na revisão o rótulo do SR Legacy virou "SR Legacy (Standard Reference)" (o código continua `SR Legacy`) e as
variáveis do FE `fdcId` viraram `usdaFoodId`, para o padrão não casar o que não é leitura de payload. Os arquivos
novos estão no `ESCOPO_NUTRICAO` (`test_arquivos_das_fontes_externas_estao_no_escopo_de_nutricao`);
`nutrition_external_foods.py` entrou em `MODULOS_DE_MOTOR` (`relogio_sem_fuso`). Nenhuma allowlist cresceu.

**Impacto em produção.**
- Este diff não traz migração nem mudança de infra (secret, `USDA_API_KEY_SECRET_ARN` e IAM vieram no #562). A
  api-core passa a fazer chamadas HTTP de saída (as Lambdas não estão em VPC: saída direta, com precedente no
  `captcha.py`), SÓ nessas três rotas, uma por requisição, timeout de 8 s; o resto do app não muda. Sem a chave: 503.
  Limite de 30 consultas novas ao OFF por usuário e dia (429). Linhas novas em `food_items` só por ação do usuário
  (públicas, `usda`/`off`).
- Mudança de semântica: `READ_ONLY_SOURCES` passou a incluir `usda` e `off` (`can_edit_food` falso e 409 no PUT) e o
  texto do 409 mudou ("tabela publicada ou de base externa"). Linhas afetadas: 0. Antes do L3b nenhum caminho gravava
  `usda`/`off` (o POST grava `user`); no DEV com dados de produção, `SELECT source, COUNT(*) FROM food_items GROUP BY
  source` = só `taco` 597 (2026-10-17); em produção `food_items` ainda não existia (080/081 só na `develop`; retrato de 2026-10-17, antes da promoção #571).
- Pré-requisitos da promoção (feita na #571; a `main` estava antes do #562 e do #563): a promoção leva junto o #562 (apply do import do
  secret e da env) e o #563 (080/081), com `RUN_DB_MIGRATIONS=true` e dry-run das migrações pendentes (074–081). Sem a
  080 as rotas respondem 503 citando 080/081; sem o #562, 503 `usda_not_configured`. Conferir por leitura
  (`describe-secret`, sem ler o valor) que `musclefit/usda-api-key` tem versão `AWSCURRENT`, valor em texto puro (o
  usuário confirmou) e a chave KMS padrão `aws/secretsmanager` (com chave própria a role precisaria de `kms:Decrypt`);
  secret em JSON viraria 403 do USDA → 503 `usda_unavailable`, visível só no smoke.
- Smoke pós-promoção SEM escrita em produção: `GET /nutrition/foods/usda/search?q=rice` 200 (chave hidratada, saída
  e roteamento) e `GET /nutrition/foods/barcode/7899999999999` 404 `off_not_found` (GTIN válido sabidamente ausente:
  prova a saída para o OFF sem gravar linha nem gastar a cota). O `barcode/7891000100103` (Leite Moça) GRAVA uma linha
  pública `off` e gasta 1 das 30 consultas do usuário do smoke: só com o aval explícito do usuário na promoção. No
  CloudWatch da api-core, nenhum `secrets_bootstrap.load_failed` com `env=USDA_API_KEY` nem "Nutrition external foods
  router não foi carregado" (o `main.py` engole erro de import).

## 50. /volume-grupamentos por macro-grupo, grupo pai ou grupo (2026-10-17)

Pedido do usuário: "Na página /volume-grupamentos/ eu quero que as tabelas exibam os dados pelos macrogrupos ao
invés dos nomes dos grupos primários". Decisão dele: um seletor na página, "Agrupar por: Macro-grupo | Grupo pai |
Grupo", com padrão Macro-grupo e a escolha lembrada no navegador (`localStorage`, leitura e escrita em `try/catch`).
Tabela regra → dono: §3.5, linha "Grupo → grupo pai → macro-grupo por linha".

**Dono — `intelligence/methodology_volume.py`.** Nenhuma regra nova de cálculo; a resolução reusa a cadeia do motor:
- `resolve_group_hierarchy(groups, volume_class_names)`: por grupo, o PAI é `block_of_group` (a raiz do bloco, a
  cadeia inteira de `methodology_parent_group_id`, com guarda contra ciclo) e o MACRO-GRUPO é `block_volume_class_id`
  (a classe do bloco: a fatia herda a do pai, e uma fatia com classe própria usa a da raiz, como no E12).
  `volume_class_order` é a posição da classe na ordem do cadastro (`sort_order, id`).
- `VOLUME_CLASS_UNCLASSIFIED_LABEL` = "Sem macro-grupo": o balde de quem não tem classe resolvida (bloco sem
  `volume_class_id`, classe sem nome ou fora do cadastro). O relatório Markdown passou a importar daqui
  (`MACRO_GROUP_UNCLASSIFIED` é a reexportação) e `resolve_macro_groups` delega a `resolve_group_hierarchy`.
- `group_name_key`, `index_hierarchy_by_name` / `hierarchy_of_group_name`: as leituras de volume entregam o grupo
  pelo NOME (`muscle_groups.name` é UNIQUE), comparado sem caixa e sem espaços nas pontas; o realizado passou a usar
  o mesmo `group_name_key` (antes repetia a conta, mesmo resultado). Nome fora do
  cadastro (o "Outros" de exercício sem contribuição) é bloco próprio, sem id, no balde: a regra do realizado
  (`methodology_realized.evaluate_realized_window`) para grupo fora do cadastro.

**Leitura — `methodology_volume_service`.** `load_group_hierarchy()` faz UM statement fora de transação
(`db.fetch_rows`: `muscle_groups` LEFT JOIN `muscle_volume_classes`, só as colunas da hierarquia), memoizado por
requisição. `annotate_group_hierarchy(linhas, hierarquia)` acrescenta por linha `parent_group_id`,
`parent_group_name`, `volume_class_id`, `volume_class_name` e `volume_class_order` e não toca em nenhum número.

**Consumidores (só consomem).**
- `GET /analytics/muscle-groups-recent-exercise-volume` e `GET /analytics/muscle-groups-equivalent-sets-by-activity`
  (Lambda engines): todas as linhas.
- `GET /analytics/training-volume`: só `trend.items`. O `overview` não ganha os campos, porque `volume_status` e
  `min/max_weekly_sets` saem de `classify_weekly_volume_status` por grupo e não se agregam por pai nem por macro.
- Tela (`VolumeGrupamentosClient.tsx` + `lib/volume-grouping.ts`): `groupingKeyOf` escolhe a chave da linha pelo
  nível (id do pai ou da classe; o rótulo vem da API), `compareVolumeGroups` ordena (macro-grupos na ordem do
  cadastro e o balde no fim; pai e grupo em ordem alfabética, como antes) e as tabelas somam o que a API entregou.
  Sem mapa grupo → pai ou grupo → macro e sem leitura de `methodology_parent_group_id` no FE. Vale para as quatro
  tabelas (comparativo por sessão, séries equivalentes, comparativo semanal, por equipamento), a variação por
  grupamento e o gráfico de repetições, o gráfico "T-0 vs T-1" e o mapa de calor. Os cards de resumo e o "Volume
  total" seguem por grupo em qualquer nível: o T-k é contado por linha, e somar por macro mudaria esses totais com o
  seletor.

**Somar por chave é apresentação** (consulta ao guardião dos motores antes da implementação):
- volume (kg), séries brutas, repetições e o RPE médio ponderado por séries vêm de uma PARTIÇÃO (cada exercício cai
  num grupo só, `COALESCE(primário, secundário, 'Outros')`): a soma por pai ou macro não conta nada em dobro;
- séries equivalentes (régua P+S+E, estabilizador incluso) por pai ou macro são a soma dos grupos, a mesma do
  realizado (`days[].effective_sets`, soma simples dos membros do bloco) e dos subtotais do relatório. A junção
  "fatia + pai com o maior peso" (`block_contributions`) é da régua da METODOLOGIA (modos de contagem, sem
  estabilizador) e não se aplica aqui. No DEV, 10 exercícios têm contribuição na fatia e no pai ao mesmo tempo
  (38, 81, 110, 113, 122, 123, 124, 126, 128, 129): nos níveis pai e macro eles contam nos dois, e a legenda da
  tabela diz isso. Mudar isso seria uma regra nova no dono, valendo também para o realizado e o relatório.

**Guarda.** Padrão novo `rotulo_sem_macro_grupo`, que nasce vazio: o rótulo vai ao FE pelo bloco `methodology_volume`
de `motor-constants.generated.json` (`unclassified_volume_class_label`, lido em `volume-counting-mode.ts` como
`UNCLASSIFIED_VOLUME_CLASS_LABEL`), e as 4 cópias do FE (fallbacks de `counting-mode-comparison.ts`,
`methodology-volume.ts` e `volume-classes.ts`, título do bloco da aba admin) passaram a ler dali, com o mesmo texto.
Nenhuma allowlist cresceu.

**Impacto em produção.**
- Payload: só campos aditivos por linha nas três rotas; o SQL de leitura delas não mudou, então os números por
  grupo são os mesmos. Conferido no DEV (dados de produção do usuário 1, só SELECT, 2026-10-17), código da `develop`
  contra o desta branch nas mesmas linhas: todos os campos antigos idênticos; 384 linhas de volume recente e 457 de
  séries equivalentes, com os mesmos totais nos três níveis (674.933,09 kg, 1.262 séries e 1.938,43 séries
  equivalentes). Na tela (`next dev`), a variação por grupamento soma o mesmo nos três níveis.
- Tamanho das respostas (DEV, sem compressão): volume recente `limit=400` 70,5 → 119,4 KB; séries equivalentes
  `limit=400` 44,5 → 103,1 KB; `training-volume?days=70` 158,8 → 187,4 KB (o `overview` não muda).
- Leitura em PRODUÇÃO (2026-10-17, um SELECT pela Data API, sem escrita): o cadastro é o mesmo do DEV, 20 grupos;
  pelo dono, Grandes 6, Pequenos 8, Outros 5 e "Sem macro-grupo" 1 (Core, 55, sem pai nem classe de propósito desde
  a 055); fatias Antebraço anterior, Costas meio, Peitoral inferior e Panturrilha anterior no pai certo; nenhum grupo
  chamado "Outros"; nenhum ciclo. Os aliases do LEFT JOIN chegam no `label` da Data API, que `_column_names` lê
  primeiro, e `sort_order` chega `int`.
- Custo: +1 SELECT por requisição nas três rotas (tabelas de 20 e 3 linhas), sem transação. A tela chama o volume
  recente duas vezes (por `limit` e, no mapa de calor, por intervalo).
- Tela: o padrão de `/volume-grupamentos` passa a ser Macro-grupo (mudança visível na promoção); cards de resumo e
  "Volume total" continuam por grupo.
- Integração: `test_rotas_paridade.py::test_hierarquia_por_linha_identica_nos_dois_backends` roda as três rotas
  com pai e classe preenchidos nos dois backends (só com `ci-completo` ou na promoção).
- Sem migração e sem mudança de infra; o SQL só lê colunas da 055/057, já em produção.

## 51. "Bom para" na prontidão por grupo (2026-10-17)

Pedido do usuário: na tabela "Prontidão por grupo" de `/dashboard/recovery`, uma coluna "Bom para" com a data e a
hora em que o músculo estará pronto para novo estímulo (a coluna "Recomendação" saiu da tabela para abrir espaço).
Linha no §3.2; padrão `bom_para_paralelo` no §5.

**Regra.** "Pronto para novo estímulo" é o mesmo estado da coluna "Estado": `readiness_status = READY`. O instante é o
primeiro minuto em que a projeção do modelo classifica o grupo READY. O dono é `recovery_projection.ready_forecast`,
porque `recovery_projection.py` já é o dono da data futura (§3.2). Ele não tem fórmula nova: cada instante candidato
passa por `project_recovery_state` (tempo × densidade × sinais, com `classify_readiness`), e uma busca binária em
minutos (`READY_AT_RESOLUTION_MINUTES` = 1) acha o primeiro READY até `READY_SEARCH_HORIZON_HOURS` (= maior entre
`RECOVERY_MAX_HOURS` e `DENSITY_WINDOW_HOURS`, 72 h). A busca vale porque a projeção é monótona no tempo: o tempo de
recuperação só cresce, a densidade só cai e os sinais não mudam até a próxima sessão.

- Grupo já READY, inclusive sem sessão na janela: sem instante, rótulo `READY_NOW_LABEL` ("Pronto").
- Grupo que não fica READY no horizonte: sem previsão (sem instante e sem rótulo; a tela mostra "—"). É o caso de um
  sinal que só uma nova sessão do grupo limpa (`SIGNALS_POLICY`): dor articular, dor muscular a partir de
  `MUSCLE_PAIN_SIGNAL_MIN`, queda de desempenho ou de repetições.
- Sessão sem instante (`hours_since_trained` ausente): também sem previsão. O retrato mede o tempo pelos dias
  (`days_since_trained × 24`), e a projeção partiria de zero hora e atrasaria o instante.
- O instante é arredondado PARA CIMA no minuto cheio (a tela mostra hh:mm; como a projeção é monótona, o minuto
  seguinte continua READY).

**Por que não `agora + remaining_hours`.** `remaining_hours` mede até 100% do tempo estimado, mas o grupo vira READY
antes, quando a prontidão ajustada passa de `READY_MIN_ADJUSTED_READINESS`. Com essa conta, um grupo que já aparece
como "Pronto" na coluna "Estado" teria uma data futura, e um grupo com dor teria uma data em que ele continuaria
bloqueado. As colunas "Falta" (até 100% do tempo) e "Bom para" (até READY) respondem perguntas diferentes, e as duas
vêm do modelo.

**Fuso (§12).** O instante sai na hora de parede do usuário (naive, a mesma base de `hours_since_trained`), e
`time_windows.user_wall_to_instant` anexa o fuso do usuário. A API devolve ISO com deslocamento
(`2026-10-18T22:30:00-03:00`), e a tela formata no fuso de `window.timezone`, sem fazer conta
(`dashboard-format.fmtWeekdayDateTime`, por exemplo "dom 18/10 22:30").

**Consumidor.**
- `recovery_signals_service.build_recovery_signals` lê o "agora" UMA vez, com `time_windows.user_now(window.timezone)`,
  e o repassa ao overview (`_readiness_items(user_id, now_local=…)` → `get_recovery_overview(now_local=…)`). O
  `hours_since_trained` e o "Bom para" partem do mesmo instante, no padrão de `MuscleRecoveryService.user_now`.
- `_group_out` chama `ready_forecast` por `_ready_out`, que segue a regra de `_readiness_items`: uma falha vira "sem
  previsão" com o evento `dashboard.ready_forecast_unavailable` e não derruba a tela, a visão geral nem o relatório.
- Dois campos aditivos em `RecoveryGroupOut` (contrato fechado): `ready_at` (`AwareDatetime | None`: um instante sem
  fuso é erro de contrato, ao contrário dos outros instantes do payload, que são naive) e `ready_label`
  (`str | None`).
- A visão geral e o relatório Markdown, que também chamam `build_recovery_signals`, recebem os campos e os ignoram.
- `metric_versions` não muda (`fullbody-no-reps-v1`), porque a fórmula da prontidão é a mesma e só ganhou uma
  leitura derivada.
- O campo `recommendation` continua na API: o cartão do celular ainda o mostra.

**Fica fora (pré-existente, registrado na revisão do guardião).**
- O "Reavaliar em cerca de X h" da avaliação (`recovery_signals_service._reassess_after_hours`) usa o maior
  `remaining_hours` dos grupos não READY. Isso inclui grupo preso por sinal, ou seja, segue a semântica de "Falta"
  (100% de tempo), não a de "Bom para". Ele pode dizer "~20 h" quando o "Bom para" do mesmo grupo é daqui a 8 h.
- `insight_rules.py` calcula `days_to_reassess` com `72.0` escrito à mão (é `RECOVERY_MAX_HOURS`).
- Os dois são candidatos a consumir `ready_forecast` e o nome da constante num PR próprio.
- O rótulo "Pronto" existe no dono (`READY_NOW_LABEL`) e, desde antes, no rótulo do status READY de
  `frontend/src/lib/dashboard-status.ts`. Se um mudar, mude o outro. A alternativa, um código fechado
  (`ready_state`) com a tela usando o rótulo do status, fica como melhoria.

**Guarda.** O padrão `bom_para_paralelo` nasce com allowlist vazia. Ele pega o instante refeito como soma de
horas restantes ou estimadas a um instante (`timedelta(hours|minutes|seconds=…remaining_hours…)`,
`remaining_hours * 3600`/`36e5`/`3600e3`, `3600 * remaining_hours`) em py e ts. É proibição pura: o dono não soma
horas, faz a busca pela projeção. A guarda é por linha, então um `timedelta(` quebrado em várias linhas escapa. Não
há alternativa para SQL, porque `remaining_hours` não é coluna do banco.

**Impacto em produção.**
- Os campos são novos e nenhum valor existente muda. Não há migração nem SQL novo: o `recovery-signals` não faz
  consulta a mais.
- A tela tirou a coluna "Recomendação" da tabela de desktop e passou a usar fonte `xs` sem quebra de linha. Os
  outros consumidores de `get_recovery_overview` (ficha, Modo Treino, planner, prompts) não mudam.
- **Medido com os dados de produção do usuário 1 no DEV** (`prod_to_dev`, só SELECT, 2026-10-17), rodando o
  `build_recovery_signals` deste PR contra o MySQL do DEV. São 19 grupos, e nenhum quebra as regras: READY →
  "Pronto" sem instante; não READY sem sinal → instante depois de agora e no máximo em agora + `remaining_hours`
  (+1 min); sinal persistente → "—".

  | "Agora" | READY → "Pronto" | Não READY sem sinal → data | Sinal persistente → "—" |
  |---|---|---|---|
  | real (2026-10-17 18h20) | 17 | 0 | 2 (Panturrilha: `EXCESSIVE_REP_DROP`; Peitoral inferior: `PERFORMANCE_DROP`) |
  | última sessão + 2 h (2026-10-10 11h22) | 10 | 7 | 2 |
  | última sessão + 20 h (2026-10-11 05h22) | 12 | 5 | 2 |

  - O mesmo grupo dá o mesmo instante nos dois "agora" simulados (Glúteos 11/10 23h48, Quadríceps 12/10 12h19,
    Bíceps e Costas 11/10 19h16): a previsão não anda com o relógio.
  - Com +2 h, "Costas meio" (READY com 3,9 h de "Falta") e "Antebraço" (READY com 0,3 h) saem "Pronto". Com
    `agora + remaining_hours` eles teriam uma data futura ao lado do selo "Pronto".
  - O "Bom para" do Quadríceps (12/10 12h19) vem cerca de 17 h antes dos 100% de tempo (13/10 05h19).
- **Custo medido.** O `ready_forecast` levou de 0,1 ms a 2,3 ms no total dos 19 grupos, contra 546 a 725 ms do
  `build_recovery_signals` inteiro no DEV. No microbenchmark sintético, o grupo não READY custa 0,31 ms; com 100
  grupos, o pior caso fica abaixo de 31 ms.
- **Contrato aditivo e ordem de deploy indiferente.** Os campos têm default `None`. Um frontend antigo ignora os
  campos, e um frontend novo com backend antigo mostra "—".
- **Na promoção.** A paridade pymysql × Data API (`test_payload_identico_nos_dois_backends`, com o relógio
  congelado de `time_windows`, que também congela o novo "agora") e o build da imagem real (`api/Dockerfile`) rodam
  no CI da promoção. No smoke, `/analytics/dashboard/recovery-signals` e `/analytics/dashboard/overview` devem dar
  200, e o `ready_at`, quando presente, deve vir como `AAAA-MM-DDTHH:MM:00-03:00`.

## 52. Nutrição — Lote L4: diário de refeições (2026-10-17)

Plano: [`docs/nutricao/PLANO_NUTRICAO_2026-10-14.md`](../nutricao/PLANO_NUTRICAO_2026-10-14.md) §5 (E2–E4, E9),
§6.2, §7, §8.1, §10, §13, §14 e §15 (R8). Tabela regra → dono: §3.7 (linhas "Matemática da refeição", "Semana",
"Diário", "Materialização", "kcal do treino"). Sem parse por IA (`/nutrition/meals/parse`, `MealParseModal`): L5.

**Schema** (migrações 082 e 083).
- `meals` (082): `user_id` (FK `users` ON DELETE CASCADE), `local_date`, `meal_slot` (`diary.MealSlot`), `eaten_at`
  TIME NULL, `source` (`diary.MealSource`; `manual` no L4), `description_raw`/`ai_job_id` (L5), `notes`, os totais
  CONGELADOS pelo motor com os códigos de `meal_math.NUTRIENT_FIELDS` (`energy_kcal`, `protein_g`, `carbohydrates_g`,
  `total_fat_g`, `fiber_g`, `sodium_mg`; por campo a soma do que se conhece, NULL sem nenhuma contribuição),
  `totals_incomplete_json` (campos com contribuição desconhecida), `totals_nature`, `item_count`, `engine_version`,
  `created_by`/`updated_by` lógicos; `KEY (user_id, local_date)`.
- `meal_items` (082): `meal_id` (FK `meals` ON DELETE CASCADE), `position`, `food_id`/`portion_id` lógicos, `item_label`,
  `quantity` + `unit` (como o aluno informou), `amount` + `basis` (quantidade NA BASE do alimento, `resolve_amount`),
  os nutrientes do item pelo motor (NULL = a tabela não informa; zero inferido fica 0 com a limitação), `nature`,
  `match_confidence` (L5), `limitations_json`, `catalog_snapshot_json` (o alimento — valores por 100, base, porção do
  rótulo, citação — e a porção usados na gravação, E4), `engine_version`.
- `nutrition_days` (083): água (`water_ml`), fome (`hunger`, 1–5) e anotação por (aluno, dia), `UNIQUE`; FK `users`.
- Desvios do rascunho do §6.2 (o plano registra o ajuste): nutrientes com os códigos da RDC 429 (como a 080) no lugar
  de `kcal`/`carbs_g`/`fat_g`; `amount` + `basis` no lugar de `grams_resolved` (alimento por 100 ml soma em ml, como
  `food_portions.amount`); `portion_id`, `item_count`, `totals_incomplete_json`, `limitations_json` e `nature` por item
  novos; `unit` com os códigos de `meal_math.Unit` (porção cadastrada por `portion_id`, não `portion:<id>`).

**Motor.**
- `meal_math`: `Nutrients` (campos = códigos da RDC 429), regra do macro ausente (`complete_missing_nutrients`,
  decisão 1), `resolve_amount` na base do alimento, `PORTION_UNITS` e `portion_amount_for` (porção cadastrada; sem
  ela, `portion` usa a porção do rótulo e `unit` fica sem conversão), `item_nutrition`, `NutritionTotals` com
  `incomplete_fields`, `sum_items`, `combine_totals`, `totals_from_stored`.
- `diary` (módulo novo; o diário não tinha dono e o `MealSlot` saiu de `labels`, que o reexporta): `MealSlot`,
  `MealSource`, `MEAL_MAX_ITEMS` (30, default de desenho), `ITEM_AMOUNT_MAX` (= `catalog.PORTION_AMOUNT_MAX`),
  `HUNGER_SCALE` (1–5), `WATER_ML_RANGE` (0–10.000 ml, default de desenho), `validate_meal_header`,
  `item_amount_errors`, `validate_day_log`, `meal_sort_key`.
- `weekly_budget`: `target_macros`, `intake_macros` (dia registrado só com energia + 3 macros completos),
  `BALANCE_FIELDS`, `day_balance`, `week_report` e a limitação `intake_incomplete_days`.
- `daily_target`: `TargetAction`, `diary_target_action`, `diary_window`, `diary_target_notice`,
  `backfill_limitations`, `backfill_marker`, `was_backfilled`, `LIMIT_TARGET_BACKFILLED`,
  `LIMIT_TARGET_FUTURE_BEYOND_WEEK`.
- `energy_expenditure`: `compare_training_kcal` (informada × MET × a usada pela prioridade; `sides` na ordem de
  `TRAINING_KCAL_NATURE_PRIORITY` com `is_used`, para a tela não escrever código de natureza).
- `labels`: `UNIT_LABELS`, `HUNGER_LABELS`, o texto de cada código novo (números interpolados do dono) e os
  avisos do diário em `NOTICE_LABELS` (`target_frozen`, `target_backfilled`, `intake_incomplete_days`,
  `training_unknown_future_day`, com o texto da limitação); `export_for_frontend` (60 chaves com o L3b):
  `unit_labels`, `portion_units`, `nutrient_fields`, `energy_field`, `item_nutrient_fields`, `hunger_levels`,
  `hunger_labels`, `water_ml_range`, `meal_max_items`; espelho `nutrition-constants.ts` (`UNIT_OPTIONS`,
  `PORTION_UNITS`, `NUTRIENT_FIELDS`, `ITEM_NUTRIENT_FIELDS`, `ENERGY_NUTRIENT`, `HUNGER_OPTIONS`, `WATER_ML_LIMITS`,
  `MEAL_ITEMS_LIMIT`), paridade no vitest.
- `ENGINE_VERSION` inalterada: nenhuma conta de meta mudou.

**Consumidores** (só consomem). `api/app/nutrition_meals.py`: `POST /nutrition/meals`, `PUT/DELETE /{id}`,
`POST /preview` (sem gravar); valida no motor (400 `invalid_meal` com os códigos e o índice do item), lê o catálogo,
monta o snapshot e grava o que o motor devolveu. `api/app/nutrition_days.py`: `GET/PUT /nutrition/days/{date}` e
`GET /nutrition/weeks`; a proveniência da kcal de cada sessão vem de `domain/calories_source.resolve_calories_source`.
`nutrition_targets_service.ensure_diary_targets`: pergunta ao motor o que fazer com a meta de cada dia lido e grava a
retroativa com `INSERT ... ON DUPLICATE KEY UPDATE id = id` (uma vez; nunca sobrescreve). `nutrition_access`:
`can_log_food`/`can_log_portion`. `nutrition_schema.require_diary_tables`: 503 citando 082/083. Tela `/nutricao`
(`NutricaoClient`, `DayHeader`, `WeekStrip`, `TargetVsIntakeCard`, `TrainingKcalRow`, `WaterHungerInputs`,
`MealList`/`MealCard`, `MealEditor` com `FoodSearch`; `lib/nutrition-meal-form.ts` só monta o corpo): números,
totais, saldo, fração da meta e semana vêm prontos da API; o total incompleto aparece como mínimo (`≥`).

**Decisões** (Tech Lead, 2026-10-17, e do lote).
1. **Macro ausente nunca vira 0 em silêncio** (`complete_missing_nutrients`): proteína/carboidrato/gordura ausentes
   valem 0 só quando a energia é conhecida e os macros conhecidos a explicam dentro de `ATWATER_TOLERANCE_SHARE`
   (`macro_inferred_zero:<campo>`); fibra ausente vale 0 só com carboidrato 0 (`fiber_inferred_zero`: na TACO e no
   USDA o carboidrato é por diferença e contém a fibra — extensão do lote, mesma lógica de "o resto da energia não
   cabe"); o resto fica desconhecido (`nutrient_unknown:<campo>`) e o total sai com o campo incompleto
   (`total_incomplete:<campo>`). Medido nas 597 linhas da TACO (seed da 081): os 8 óleos/azeites inferem proteína e
   carboidrato 0 (884 kcal × 900 de Atwater, 1,8%) e fibra 0; dos 220 itens com só a fibra ausente, 148 têm
   carboidrato 0 e ganham fibra 0, e 72 (laticínios, embutidos, preparações) ficam com a fibra desconhecida; 6 itens
   sem energia (4 em reavaliação — leite integral, leite desnatado UHT, iogurte sabor abacaxi, coco verde — e os 2
   sais) deixam a energia do total incompleta; a aguardente (215,66 kcal, nenhum macro) não infere nada. Com o M85
   (§48.1, 084) os 2 leites vêm do USDA: ficam 4 itens sem energia (iogurte sabor abacaxi, coco verde e os 2
   sais) e 74 com a fibra desconhecida (os leites: fibra `NA`); a regra não mudou.
2. **Dia passado sem meta (R8)**: o dia lido pelo diário que congelou sem meta é calculado com os dados disponíveis e
   gravado UMA vez, marcado nas colunas JSON que a 079 já tem (`limitations_json` com `target_backfilled`,
   `inputs_json.materialization = {kind: backfill_after_freeze, computed_on}`): a 079 não foi editada e nenhuma migração
   nova foi necessária (a 084 é do M85, §48.1). Congelado com meta só lê; não congelado segue o fluxo do L2b (a visita ao diário de hoje/ontem mantém a
   kcal do Intervals em dia); futuro além da semana atual sem meta não cria. Sem job agendado nem infra.
3. **Itens só do catálogo** (`food_items`: TACO, do usuário, USDA, OFF), natureza `DERIVED`; unidades `g`/`ml`,
   porção cadastrada (`portion_id`), porção do rótulo (`portion` sem `portion_id`) ou medida caseira do motor; `unit`
   sem porção cadastrada é `unit_without_conversion`. `MIXED`/`ESTIMATED` só com o L5.
4. **Nutrientes congelados por item** (E4) com `catalog_snapshot_json`; editar o alimento no catálogo não muda
   refeição antiga, e o item mantido na edição (`id` + mesmo alimento) reusa o snapshot.
5. **Acesso** por `nutrition_access.resolve_target_user` (o próprio; admin/PT para qualquer aluno, sem vínculo,
   D-L); outro aluno recebe 403 (IDOR coberto). Alimento do diário: público, do aluno ou de quem registra (admin/PT
   com o próprio cadastro: o snapshot copia o nome para o diário do aluno).
6. **kcal do treino no dia**: informada (`activities.calories_kcal`, soma só com todas as sessões com kcal,
   `day_context_for`) com o selo de proveniência do dono `calories_source`, e a estimada por MET do motor
   (`compare_training_kcal`, nunca gravada); a tela só exibe.
7. **Total incompleto é um mínimo**: o valor é a soma do que se conhece e o campo vai para `incomplete_fields`; o dia
   com energia ou macro incompleto fica fora do banco e da aderência (`intake_incomplete_days`) e o saldo do
   nutriente incompleto não é calculado.

**Guarda.** Padrões novos `diario_nutricao` (constantes do diário, da semana e do congelamento redefinidas com número
e a tabela de medidas caseiras reescrita; repo inteiro; nasce vazia) e `soma_nutrientes` (soma de nutrientes por
`sum(`/`SUM(`/`.reduce(`/`+=`/`x = x + …` e escala por 100 escrita à mão no escopo de nutrição; donos `meal_math`,
`weekly_budget` e `macros` (`Macros.plus`); proibição pura no dono; nasce vazia). `api/app/nutrition_days.py` entrou em `MODULOS_DE_MOTOR` (`relogio_sem_fuso`); os arquivos novos estão no
`ESCOPO_NUTRICAO` (`test_arquivos_do_diario_estao_no_escopo_de_nutricao`). Nenhuma allowlist cresceu.

**Riscos para os próximos lotes.**
- **Leite integral da TACO sem energia** ("em reavaliação"): RESOLVIDO pelo M85 (§48.1, migração 084) — os campos
  `*` dos 2 leites foram preenchidos com o USDA FoodData Central e um café com leite fecha o dia. Continuam sem
  energia o iogurte sabor abacaxi e o coco verde (sem equivalente adotado, decisão do usuário) e os 2 sais (`NA`).
- **Dashboard (L7)**: agregar pelos totais gravados COM a lista de incompletos (ou pelo motor); `SUM(energy_kcal)`
  puro cai na guarda `soma_nutrientes`.
- **GET que grava**: o diário materializa a meta (como o `GET /nutrition/me/plan` do L2b) e grava a retroativa; com
  plano publicado, abrir uma semana antiga grava até 7 linhas, uma vez.
- **Busca no editor** usa a identidade de quem registra (admin/PT não veem os alimentos privados do aluno na busca).

**Impacto em produção.**
- **Schema.** Três tabelas novas: 082 = 2 `CREATE TABLE` (`meals` antes de `meal_items`), 083 = 1 `CREATE TABLE`;
  0 statements na reexecução; forma divergente aborta antes do primeiro statement; FKs só para `users` e
  `meal_items → meals`. Leitura em produção (Data API, só SELECT, 2026-10-17): `information_schema.TABLES` sem
  `meals`, `meal_items`, `nutrition_days` (nem `nutrition_plans`, `nutrition_daily_targets`, `food_items`: 0 linhas);
  `users.id` BIGINT PK InnoDB; `schema_migration_files` com 73 arquivos, a última versão aplicada é a 073 (retrato
  de antes da promoção #571 (17/10/2026, 23h10 UTC, deploy 39077773130), que aplicou 074–083).
- **Rotas e tela.** Dois routers novos sob o `{proxy+}` da api-core (`/nutrition/meals`, `/nutrition/meals/preview`,
  `/nutrition/meals/{id}`, `/nutrition/days/{date}`, `/nutrition/weeks`), 503 citando 082/083 antes das migrações, e a
  página estática `/nutricao`. A promoção expôs o menu "Nutrição" (plano, alimentos e diário) a todos pela primeira
  vez (promoção #571 (17/10/2026, 23h10 UTC, deploy 39077773130)).
- **Escrita.** Só nas tabelas novas e em `nutrition_daily_targets`, e só para aluno com plano publicado em vigor no
  dia aberto: o dia não congelado segue o fluxo do L2b (o GET do diário cria a meta ou a REGRAVA quando o hash das
  entradas muda, como o `GET /nutrition/me/plan`); o dia congelado sem meta é gravado uma vez
  (`INSERT ... ON DUPLICATE KEY UPDATE id = id`, nunca sobrescreve). Vale também quando admin/PT abre o diário do
  aluno. Na promoção o efeito é nulo: a 079 nasce vazia e não há plano publicado.
- **Promoção.** FEITA: promoção #571 (17/10/2026, 23h10 UTC, deploy 39077773130). `RUN_DB_MIGRATIONS=true`. A `main` estava na 073, e o dry-run listou 074–083 (25 statements): 074 = 2
  `ALTER` (`users.height_cm`, `activity_level`), 075 = 1 `MODIFY` (ENUM de `body_measurements.site`), 076 = 1
  `ALTER` (`activities.calories_source`), 077 = 2 `UPDATE` (`users.sex`, 2 linhas), 078 = 1 e 079 = 1 `CREATE`,
  080 = 2 `CREATE`, 081 = 12 `INSERT` (597 linhas da TACO), 082 = 2 e 083 = 1 `CREATE`. Depois de aplicar, o
  `status` mostra todas aplicadas e um novo dry-run lista 0 statements. Com o M85 (§48.1), a próxima promoção leva
  só a 084 (1 `ALTER` + 2 `UPDATE` = 3 statements).
- **CI.** Rótulo `ci-completo` (SQL e migração): integração nos dois backends
  (`test_nutrition_diary_dataapi.py`) e smoke das imagens (082/083 em `/var/task/db/migrations`) verdes antes do merge.
- **Smoke pós-promoção, sem escrita**, com um usuário SEM plano publicado: `POST /nutrition/meals/preview` com um
  alimento da TACO, `GET /nutrition/days/{hoje}` e `GET /nutrition/weeks`, todos 200 (404 = router não carregado: o
  `main.py` engole erro de import); `SELECT COUNT(*)` de `meals`, `meal_items` e `nutrition_days` = 0 e de
  `nutrition_daily_targets` igual antes e depois (sem plano, nada é gravado —
  `test_sem_plano_em_vigor_nao_ha_meta_nem_escrita`); no CloudWatch da api-core, nenhum "Nutrition diary routers não
  foram carregados".
- **Reversão.** Reverter o código é seguro: as tabelas são aditivas e o código antigo não as lê. O `down` da 082/083
  não é acionável pela Lambda de migrações; desfazer o schema exige migração nova com `DROP`, que perde o diário
  gravado. Sem mudança de infra.

## 54. Nutrição — Lote L5a: uso e cota de IA (2026-10-18)

Plano: [`docs/nutricao/PLANO_NUTRICAO_2026-10-14.md`](../nutricao/PLANO_NUTRICAO_2026-10-14.md) §5 (E7, E8), §6.1,
§6.2, §8.3, §13 e §14. Tabela regra → dono: §3.7 (linha "Uso e cota de IA"). É a base que o L5b (§55, texto da
refeição) e o L6 (§56, foto do rótulo) consomem em paralelo depois: neste lote não há rota, tela, prompt nem chamada
de IA.

**Dono novo.** O mapa não tinha dono de cota ou uso de LLM: a periodização (`periodization/logs.DAILY_GENERATION_LIMIT`,
10/dia) e os cards (`exercise_card_service.DEFAULT_MAX_PER_DAY`, 100/dia) têm cada um a sua e não migram agora (E7);
o custo só existia no log `llm.usage` (14 dias) e, na periodização, por preço em variável de ambiente que a infra não
publica. Nasce `domain/nutrition/ai_usage.py` (Python puro, sem relógio): `AiFeature` (`meal_parse`, `label_photo`; o
L5b/L6 usa o mesmo código como rótulo `feature` do `llm.usage` e chave do teto de saída em `openai_client`),
`AiUsageStatus`, a regra de fechamento (`OPEN_STATUS`, `SWEEP_PRESUMED_STATUS`, `worker_may_close`), `DAILY_QUOTA`
(`MEAL_PARSE_DAILY_QUOTA` = 30, `LABEL_PHOTO_DAILY_QUOTA` = 10, plano §8.3, defaults de desenho), `quota_status` →
`QuotaStatus`, `FEATURE_JOB_TYPE` (`MEAL_PARSE_JOB_TYPE` = `nutrition-meal-parse`, `LABEL_PHOTO_JOB_TYPE` =
`nutrition-label-ocr`, plano §8.3; sem anotação de tipo de propósito, para o teste do M73 resolver
`create_job(<constante>)`), `feature_for_job_type`, o código `ERR_AI_QUOTA_EXCEEDED`, a tabela
`MODEL_PRICES_USD_CENTS_PER_MTOKEN` e `estimate_cost_usd`. Os textos ficam em `labels` (`AI_FEATURE_LABELS`,
`ai_quota_exceeded_label` e o texto de `ai_usage.ERR_AI_QUOTA_EXCEEDED` em `LIMITATION_LABELS`). O "hoje" do usuário no
relógio do banco ganhou o helper `time_windows.user_day_start_db_for(conn, user_id)` (dono do fuso, §12), usado pela
cota e pelo limite do Open Food Facts (que tinha a mesma composição à mão). O bloco `nutrition` do JSON gerado não
mudou (nenhuma tela consome a cota ainda).

**Schema** (migração 085). `ai_usage_log`: `user_id` (FK `users` ON DELETE CASCADE), `feature`, `job_id` (lógico, o
job do DynamoDB), `prompt_version`, `model`, `status`, `error_code`, `tokens_in`, `tokens_out`, `image_count`,
`cost_usd` DECIMAL(10,6), `started_at` (a reserva, no relógio do banco: é o relógio da cota), `finished_at`,
`updated_at`; `KEY (user_id, feature, started_at)` (cota) e `KEY (status, started_at)` (sweep). Desvios do rascunho
do §6.2 (o plano registra): `error_code` (o motivo da falha não tinha coluna) e `updated_at`; `started_at` faz o papel
do `created_at` da regra do §6.1, sem coluna repetida; o custo com 6 casas, porque uma leitura custa frações de
centavo. Sem default de status nem enumeração na migração (vocabulário do motor).

**Serviço** (`api/app/nutrition_ai_quota.py`, consumidor; SQL igual nos dois backends). Contrato para o L5b e o L6:
- rota (antes do 202): `usage_id = open_usage(user_id=, feature=, prompt_version=, model=, image_count=)` → uma
  transação curta: trava da linha do usuário como PRIMEIRO statement (`SELECT ... FOR UPDATE`, como a trava por
  exercício dos cards), contagem do dia (a primeira leitura consistente, depois da trava), 429
  `ai_quota_exceeded` com `{feature, limit, used, remaining, allowed}` e o texto do dono, ou a linha `pending`; 503
  `ai_usage_table_missing` antes da 085 (`nutrition_schema.require_ai_usage_table`). Depois, com
  `from .domain.nutrition.ai_usage import MEAL_PARSE_JOB_TYPE`: `async_jobs.create_job(MEAL_PARSE_JOB_TYPE, ...)`
  (o nome sem `ai_usage.` na frente: a regex do teste do M73 não lê o ponto e não resolveria a constante),
  `link_job(usage_id, job_id)` (best-effort) e `async_jobs.invoke_async(..., {"ai_usage_id": usage_id, ...})`. Em
  `async_jobs.JOB_TYPE_LAMBDA` a chave é `ai_usage.MEAL_PARSE_JOB_TYPE` (lida em tempo de execução; a guarda recusa o
  literal, então o `async_jobs` importa o dono, que é puro);
- worker: `finish_usage(usage_id, status=, model=, tokens_in=, tokens_out=, cached_tokens_in=, error_code=)`, que
  nunca levanta (a auditoria não derruba o job) e, sem `conn`, roda o sweep DEPOIS do commit do fechamento; com
  `conn` só fecha (o sweep numa segunda conexão esperaria a trava da linha ainda não commitada até o lock wait
  timeout), e quem passa `conn` chama `sweep_stale_pending` depois de commitar;
- tela: `usage_today(conn, user_id=, feature=).to_dict()` ("x de 30 hoje");
- com a conexão do chamador: `reserve_usage` (só como primeira coisa de uma transação curta, como faz `open_usage`),
  `count_usage_today`, `attach_job`, `sweep_stale_pending`.

**Decisões** (do lote, com as revisões do guardião e do impacto em produção).
1. **Toda tentativa conta** (qualquer status), como a periodização e os cards: uma falha também pode ter gastado
   tokens, e contar só sucesso deixaria um laço de falhas sem teto. O usuário perde a tentativa que falhou por
   nossa causa; com 30/dia no texto, aceito.
2. **Dia da cota = dia do usuário** no fuso do perfil: o serviço conta `started_at >=
   time_windows.user_day_start_db_for(conn, user_id)`, o mesmo critério do limite do Open Food Facts (§49) e da
   periodização (M76 Lote 2b, §12).
3. **Sem corrida**: a trava do usuário é o primeiro statement da transação e serializa as reservas simultâneas do
   mesmo usuário; a contagem é a primeira leitura consistente depois dela, então o retrato já inclui a reserva que
   soltou a trava, e a cota não estoura por duas abas ao mesmo tempo. Provado no PyMySQL e no dublê da Data API (que
   abre a transação com o mesmo `BEGIN`); a AWS não documenta se o `BeginTransaction` real já abre o retrato — se
   abrir, duas reservas simultâneas do mesmo usuário podem passar 1 cada da cota (risco residual baixo: a cota é
   branda). Contar com `FOR SHARE` foi descartado: as travas de gap no fim da faixa de um usuário são as do começo da
   faixa do próximo, e duas reservas de usuários diferentes (que a trava de `users` não serializa) entrariam em
   deadlock ao inserir no mesmo gap. Custo: enquanto a reserva dura, toda escrita do mesmo usuário que dependa da linha dele (INSERT em tabela
   filha, `UPDATE users`) espera. Por isso a reserva roda SEMPRE na transação curta de `open_usage` — nunca dentro de
   uma transação longa da rota, nunca em volta de uma chamada à OpenAI ou ao DynamoDB. A trava só existe quando o
   L5b/L6 chamar `open_usage`.
4. **Sweep sem agendamento nem infra, no worker**: roda a cada `finish_usage` sem `conn` (best-effort, transação
   própria, depois do commit do fechamento), fora do caminho do usuário, e é global (todos os usuários, índice `(status, started_at)`). É a
   opção "worker do job" do briefing: o cron do Intervals é de outra Lambda e o EventBridge seria infra nova. Como a
   cota conta toda tentativa, o sweep não muda decisão de cota: só fecha a auditoria, e uma linha perdida fica
   `pending` até o próximo fechamento de qualquer worker de IA da nutrição. O prazo de cada feature é o do worker
   perdido do M73 para o job type dela (`async_jobs.worker_lost_deadline_seconds`: timeout da Lambda +
   `WORKER_LOST_GRACE_SECONDS`, com paridade com `infra/*.tf`); nenhum número novo. Feature cujo job type ainda não
   está em `JOB_TYPE_LAMBDA` (hoje as duas: o teste do M73 recusa job type sem chamador) fica de fora — sem rota não
   há linha dela. Opcional no L5b/L6: chamar o sweep também quando o `GET /jobs` fechar um job da feature como
   `worker_lost` (`ai_usage.feature_for_job_type`), como a periodização faz.
5. **O relato do worker vence a presunção do sweep** (`ai_usage.worker_may_close`): o worker fecha a linha aberta ou a
   `failed` com `worker_lost` (o worker atrasado pela fila de throttling, M81, ainda pode ter rodado e gastado
   tokens); nenhum outro estado terminal é reaberto. O WHERE do UPDATE repete a regra com os valores do dono, porque
   a linha pode mudar entre a leitura e a escrita.
6. **Custo com fonte, nunca inventado**: tabela em centavos de dólar por 1 milhão de tokens (números exatos), da página
   oficial de preços da OpenAI consultada em 2026-10-08 (registrada no relatório
   auditoria de 2026-10-08 (arquivo privado), "Custo estimado"): gpt-5.4-mini, gpt-5.4-nano,
   gpt-5.4 e gpt-4o-mini. Modelo fora da tabela ou token ausente → NULL (os tokens ficam gravados); snapshot datado
   usa o preço do modelo-base; sem a contagem de tokens em cache, a entrada inteira paga o preço cheio (o custo é um
   teto). Preço muda: atualizar a tabela, `PRICE_SOURCE_URL` e `PRICES_CHECKED_ON` juntos.
7. **Divergência registrada (revisão do guardião)**: o custo da periodização (`periodization/logs.estimate_cost_usd`)
   segue a política oposta (preço em `OPENAI_PRICE_IN/OUT_PER_MTOKEN`, hoje sem valor na infra, então NULL), e
   `openai_client.IMAGE_PRICES_USD` é uma tabela com fonte, o precedente que o L5a segue. O preço por modelo não
   depende da feature: unificar num dono neutro (por exemplo `domain/llm_pricing.py`, ao lado de `IMAGE_PRICES_USD`)
   fica para o backlog; migrar a periodização muda semântica (`cost_usd` deixa de ser NULL) e exige "Impacto em
   produção" medido. Até lá, no escopo de nutrição é proibido usar o custo da periodização ou ler `OPENAI_PRICE_*`
   (guarda `cota_ia_copia_nutricao`).
   **Resolvida para a periodização no §74 (2026-10-30)**: `periodization/logs.estimate_cost_usd` consome o dono;
   a proibição da guarda no escopo de nutrição continua.

**Guarda.** Padrões novos, os dois nascem vazios:
- `cota_ia_nutricao` (repo inteiro): constantes do dono redefinidas, job type em literal, variável de cota comparada
  ou atribuída a número (`>=`, `===`, `!==`, `: int = 10`, `: 30`; `: 0` é inicialização e não casa), tabela de preço copiada (`"gpt-…": <número>`, `ModelPrice(`);
- `cota_ia_copia_nutricao` (só `ESCOPO_NUTRICAO`; proibição pura): `*_DAILY_LIMIT`, `MAX_PER_DAY`, `*PARSE/OCR/QUOTA/COTA/CALLS/REQUESTS*_PER_DAY` e `LIMITE_DIARIO` com
  número ("por dia" sozinho é unidade comum na nutrição e não casa), cota ou preço lidos de variável de ambiente (`os.environ`/`getenv`/`process.env`), `OPENAI_PRICE_*` e
  `periodization.logs`.

`api/app/nutrition_ai_quota.py` entrou em `MODULOS_DE_MOTOR` (`relogio_sem_fuso`); os dois arquivos estão no
`ESCOPO_NUTRICAO` (`test_arquivos_do_uso_de_ia_estao_no_escopo_de_nutricao`). Nenhuma allowlist cresceu. Sem padrão,
de propósito: o código `label_photo`, que também é `FoodSource.LABEL_PHOTO` do catálogo, e o literal `meal_parse`,
porque o teto do `openai_client` usa a chave da feature; o L5b/L6 cobre os dois com um teste de paridade
`AiFeature` × `LLM_OUTPUT_OBSERVED_MAX_TOKENS`. A sobrescrita da cota por Terraform também não tem padrão, porque a
guarda não varre `.tf`: fica proibida pelo §3.7.

**Para o L5b e o L6** (o que fica com eles).
- Registrar o job type em `async_jobs.JOB_TYPE_LAMBDA` com a constante do dono, na Lambda da decisão E8
  (`test_nutrition_ai_quota.test_job_type_registrado_roda_na_lambda_da_decisao_e8` confere): é isso que liga o sweep
  da feature.
- Passar `ai_usage.AiFeature.X.value` como `feature` do `openai_client`, incluir o teto em
  `LLM_OUTPUT_OBSERVED_MAX_TOKENS` com teste de paridade e fechar a linha em todos os caminhos do worker (sucesso,
  falha, recusa) e na falha do despacho da rota.
- Expor a cota à tela pela API (`usage_today`); o FE não escreve 30 nem 10.
- Nenhuma infra nova é necessária para o sweep. As envs dos modelos, o lifecycle e o IAM do S3 continuam com o L5b/L6
  (plano §12).

**Impacto em produção.**
- **Schema.** Tabela nova: 085 = 1 `CREATE TABLE`; 0 statements na reexecução (`test_migration_085_plano.py`,
  convergido; e `test_ai_usage_log_dataapi.py`, `plan()` no `information_schema` real lido pelos DOIS backends no CI);
  forma divergente aborta antes do primeiro statement; FK só para `users`. Dry-run no DEV (Docker, 2026-10-18, só
  leitura): 84 já aplicadas (001–084), a aplicar só a 085, com 1 statement.
- **Imagem.** A 085 entra na imagem `musclefit-ai-generators` (a da Lambda `musclefit-migrations`): o
  `api/Dockerfile.ai.dockerignore` libera `!db/migrations` (o `**/*.py[cod]` não pega `.py`) e o `Dockerfile.ai` faz
  `COPY db/migrations`. O smoke da imagem confere a lista 1:1 com o repositório e carrega todas as migrações `.py`
  (roda com `ci-completo`).
- **Promoção.** Produção está na 083, pelo registro da promoção #571 (deploy 39077773130, §17 do plano); a próxima
  promoção leva, esperado, 4 statements: a 084 do M85 (3, medidos no M85) e a 085 (1), com `RUN_DB_MIGRATIONS=true`.
  O PR de promoção traz a saída do dry-run mostrando 084 e 085 a aplicar (e, só leitura, a ausência de `ai_usage_log`
  no `information_schema` de produção).
- **Comportamento.** Nenhum: nenhuma rota, worker ou tela chama o serviço; a tabela nasce vazia e fica vazia até o
  L5b/L6, e a trava do usuário só passa a existir quando o L5b chamar `open_usage`. O `nutrition_schema` ganhou a
  detecção da tabela; o limite do Open Food Facts passou a compor o início do dia pelo helper novo de `time_windows`
  (mesmo cálculo, mesmo resultado).
- **CI.** Rótulo `ci-completo` (migração): integração nos dois backends (`test_ai_usage_log_dataapi.py`) e smoke das
  imagens.
- **Reversão.** Reverter o código é seguro (nada lê a tabela). O `down` da 085 (DROP TABLE) não é acionável pela
  Lambda de migrações; desfazer o schema exige migração nova com `DROP` (sem perda: a tabela está vazia até o L5b).
  Sem mudança de infra.

## 55. Nutrição — Lote L5b: leitura da refeição por IA (2026-10-18)

Plano: [`docs/nutricao/PLANO_NUTRICAO_2026-10-14.md`](../nutricao/PLANO_NUTRICAO_2026-10-14.md) §5 (D2), §8.1,
§8.3, §9 (a), §10, §12 e §13. Tabela regra → dono: §3.7 (linha "Leitura da refeição por IA"). Consome a base do L5a
(§54); o L6 (§56, foto do rótulo) roda em paralelo. Sem migração: as colunas que o L5 usa já estavam na 082
(`meals.description_raw`, `meals.ai_job_id`, `meals.source = ai_text`, `meal_items.food_id` NULL,
`meal_items.match_confidence`).

**Dono novo.** `domain/nutrition/meal_parse.py` (Python puro): o vocabulário que a IA usa (`AI_UNITS` = as unidades
de `meal_math.Unit` menos a porção, que a IA não conhece; medidas caseiras de `GENERIC_PORTIONS`), a estimativa por
100 g (`ESTIMATE_FIELDS` com os códigos da RDC 429 do catálogo, sem sódio, como no plano; `ESTIMATE_KEY`
`estimated_per_100g` montada de `PER_100G_REFERENCE`; obrigatórios = os do cadastro, `catalog.REQUIRED_PER_100_FIELDS`;
validada por `catalog.validate_food_values`, Atwater fora da tolerância vira aviso), `TEXT_MAX_CHARS` (1.000),
`CANDIDATES_PER_ITEM` (5, o número do plano), `CONFIDENCE_RANGE` (0 a 1, só informativa), `MatchStatus`
(`selected`, `choice`, `estimated`, `unmatched`) e as funções da decisão. Os limiares do casamento NÃO são daqui:
continuam em `food_text` (`MATCH_MIN_SCORE` 0,6, `MATCH_AUTO_ACCEPT_SCORE` 0,85 com a folga
`MATCH_AUTO_ACCEPT_MIN_MARGIN`); `classify_item` só lê o `auto_accept` do `Match`. A origem do item do diário
(catálogo OU estimativa, exatamente um) é regra de `diary.item_source_errors`. Textos em `labels`
(`MEAL_PARSE_MATCH_STATUS_LABELS` e as limitações `ai_estimate`, `ai_estimate_invalid`,
`quantity_from_grams_estimate`, `ai_quantity_missing`, `ai_items_truncated`, `ai_no_items`, os erros do texto e do
worker).

**Fluxo** (decisão D2: híbrido).
1. Rota (`nutrition_meal_parse.py`, api-core): `POST /nutrition/meals/parse` valida o texto e o slot no motor (400
   `invalid_meal_parse` ANTES de gastar a cota), confere as tabelas e o aluno, e segue o contrato do L5a:
   `open_usage` (429 `ai_quota_exceeded`) → `async_jobs.create_job(MEAL_PARSE_JOB_TYPE, ...)` → `link_job` →
   `async_jobs.invoke_async("WORKOUT_AI_FUNCTION_NAME", {..., "ai_usage_id"})` → **202** `{job_id, status, quota}`.
   Despacho que falha fecha a linha como `failed` (`ai_dispatch_failed`) e responde 503. A cota é de QUEM PEDE (o
   admin/PT que lê para um aluno gasta a dele: o job é dele no `GET /jobs`, M65, e o custo também); o aluno vai no
   payload. O `request` gravado no DynamoDB não leva o texto (só o tamanho). `GET /nutrition/meals/parse/quota`
   devolve `usage_today` para a tela.
2. Worker (`nutrition_meal_parse_worker.py`, Lambda `musclefit-workout-ai`, decisão E8): uma chamada estruturada
   (`nutrition_meal_parse_ai.py`, schema strict, `store=false`, timeout `LLM_TIMEOUT_LIVE_SECONDS` de 50 s, teto de
   saída da feature), `normalize_ai_items`, e para cada item a MESMA busca da tela (FULLTEXT de `food_text` sobre
   os alimentos que o diário do aluno pode usar, `nutrition_access.diary_foods_visibility`, lote
   `SEARCH_CANDIDATE_POOL`, ordem pela nota) com e sem o preparo; `best_ranking` fica com a melhor e
   `classify_item` decide. Fecha o `ai_usage_log` em todos os caminhos (sucesso com modelo e tokens; falha da IA
   com o código da OpenAI; falha da busca com os tokens já gastos; texto inválido); o handler fecha como
   `ai_worker_failed` se cair ANTES de despachar. `GET /jobs` com `worker_lost` de uma feature de IA da nutrição
   também varre o `ai_usage_log` (`ai_usage.feature_for_job_type`).
3. Tela (`MealParseModal` em `/nutricao`, `dispatchAndPollJob`): o editor da refeição abre com os itens propostos;
   a pessoa confirma (troca o alimento entre os candidatos ou pela estimativa da IA, ajusta a quantidade) e grava
   pelo `POST /nutrition/meals` de sempre, com `ai_job_id` e `description_raw` (origem `ai_text`,
   `meal_parse.meal_source`). O item estimado vai sem `food_id` e com a estimativa como a leitura entregou; a API
   valida (`validate_estimate`) e calcula (`estimated_item_nutrition`: a conta de `meal_math.item_nutrition`,
   natureza ESTIMATED, limitação `ai_estimate`; sódio desconhecido → total incompleto, nunca 0), com o snapshot
   `ai_estimate` congelado (E4). Na edição, o estimado mantido vai só com o `id` e reusa o snapshot.

**Decisões** (medidas no eval pago, abaixo).
1. **Quantidade = os gramas totais da IA** (`proposed_quantity`): a unidade contável precisa de porção cadastrada e
   a medida em volume só vira massa pela densidade do alimento, que a TACO não tem (o diário assumiria
   `DEFAULT_DENSITY_G_PER_ML`: "1 xícara de alface" virava 240 g; os gramas da IA deram 35 g). A medida dita fica
   no resultado (`ai`), e o aviso `quantity_from_grams_estimate` aparece quando a pessoa não disse em gramas.
2. **Duas buscas por item** (com e sem o preparo, `best_ranking`): o preparo distingue itens da TACO ("cozido" ×
   "cru"), mas derrubava a nota de quem não o tem no nome ("feijoada cozida" × "Feijoada": 0,57, sem candidato).
   Fica a de maior nota no primeiro; o aceite automático (nota e folga) é o do próprio `food_text` em cada busca.
3. **Candidatos = os 5 melhores pela nota dentro do lote do FULLTEXT**, não os 5 primeiros do FULLTEXT: é a busca
   calibrada na TACO no L3a (desvio do texto do §9 a, registrado no plano).
4. **Estimativa pedida sempre** (null só se a IA não fizer ideia): só é usada como proposta no `estimated` e como
   alternativa no `choice`; custa ~60 tokens de saída por item.
5. **Vocabulário do motor no schema**: unidades de `meal_math.Unit` (sem `slice`/`piece` do rascunho: fatia e
   pedaço viram `unit` com os gramas) e campos da RDC 429 (sem `kcal`/`carbs_g`/`fat_g`): nenhuma conversão de nome
   fora do dono.
6. **Modelo** `OPENAI_NUTRITION_PARSE_MODEL` (Terraform `var.openai_model_nutrition_parse`, default
   `gpt-5.4-mini`, a família "mini" das features de texto da mesma Lambda); sem a env, o código usa o mesmo
   default (paridade com o DEV, que não define a env). A variável mora em `infra/lambda_workout_ai.tf`, ao lado das
   cinco irmãs da Lambda, e não em `variables.tf` (o padrão do repositório para os modelos).
8. **Prompt sem código à mão** (revisão do guardião): os exemplos do prompt interpolam os códigos de `meal_math.Unit`
   e a unidade de quantidade não dita (`meal_parse.UNSTATED_QUANTITY_UNIT`, a mesma do fallback de
   `proposed_quantity`); `test_nutrition_prompts_snapshot.py` confere que todo código entre crases é do motor ou do
   schema. O texto renderizado não mudou (o snapshot e o eval valem).
9. **A tela não decide unidade nem base do estimado**: o bloco `meal_parse` do JSON gerado exporta `estimate_basis`;
   voltar para a estimativa restaura a quantidade e a unidade que o motor propôs. O modelo estrito
   `MealItemEstimateIn` tem paridade com `ESTIMATE_FIELDS` e `ITEM_LABEL_MAX_CHARS`.
10. **A api-core não carrega o worker no boot** (revisão de impacto): o despacho importa
   `nutrition_meal_parse_worker` só no ramo do job type (o `main.py` importa `workout_ai_generation_core` fora de
   `try`), então uma falha de import da cadeia do worker não derruba a API.
7. **Teto de saída**: `LLM_OUTPUT_OBSERVED_MAX_TOKENS["meal_parse"]` = 426 (o maior `output_tokens` medido); o teto
   efetivo é o piso de 16.384 do critério único do `openai_client`. Paridade `AiFeature.MEAL_PARSE.value` × teto em
   `test_nutrition_meal_parse_ai.py`.

**Eval pago** (`tests/eval/casos/meal_parse.json`, 9 casos escritos à mão, sem dado de usuário; critérios em
`criterios_llm.avaliar_parse_refeicao`, medidos no RESULTADO do worker com a TACO real em memória; agregado em
`resumo_parse_refeicao`; os critérios rodam sem LLM no CI em `test_nutrition_meal_parse_eval_criterios.py`).
Limiar do lote: todos os casos sem problema e nenhuma pré-seleção errada. Resultado final (`nutrition-parse-v1`,
`gpt-5.4-mini-2026-03-17`, duas rodadas seguidas): 9 de 9 casos, 27 de 27 itens com proposta aceita, 23 de 27
casados com a TACO (os 4 restantes pela estimativa da IA: café preto, whey, farofa e barra de proteína), 0
pré-seleção errada, latência de 0,9 a 4,5 s por leitura. Custo por rodada: ~10,7 mil tokens de entrada e ~2,3 mil
de saída, US$ 0,018 (≈ US$ 0,002 por leitura; o L5a grava o custo por linha). As 8 rodadas do lote (calibração do
prompt e da quantidade) somaram ~US$ 0,15.

**Guarda.** Padrão novo `leitura_refeicao_ia` (repo inteiro, nasce vazio): constantes de `meal_parse` redefinidas,
também com prefixo no nome, e a chave da estimativa em literal. Os limiares do casamento seguem com `catalogo_alimentos` e `nota_matching`; a
soma de nutrientes, com `soma_nutrientes`. Os arquivos do lote estão no `ESCOPO_NUTRICAO`
(`test_arquivos_da_leitura_por_ia_estao_no_escopo_de_nutricao`). O prompt não tem número escrito à mão
(`test_nutrition_prompts_snapshot.py`: todo número da parte fixa vem de `GENERIC_PORTIONS`, `PER_100G_REFERENCE` e
`CONFIDENCE_RANGE`, e trocar a fonte troca o prompt). Nenhuma allowlist cresceu.

**Impacto em produção.**
- **Schema (lido em produção em 2026-10-18, só SELECT pela Data API).** Nenhuma migração. As colunas que o lote
  passa a gravar já existem na Aurora com o tipo do desenho: `meals.description_raw` text NULL, `meals.ai_job_id`
  varchar(64) NULL, `meals.source` varchar(16) NOT NULL, `meal_items.food_id` bigint NULL,
  `meal_items.match_confidence` decimal(4,3) NULL. O checksum da 082 e da 083 em `schema_migration_files` é igual
  ao do repositório (`49cd76b7…` e `60e531b3…`). Linhas existentes: 1 refeição (`source = manual`, `description_raw`
  e `ai_job_id` NULL) e 1 item (`DERIVED`, `food_id` preenchido, `match_confidence` NULL). O SELECT novo do diário
  (com as 3 colunas) lê essas linhas sem mudança, e `editorFormFromMeal` deixar de filtrar `food_id` NULL não muda
  nada em produção (0 itens). `ai_usage_log` ainda não existe (a 085 vai na próxima promoção). A leitura acordou a
  Aurora do auto-pause.
- **Depende da 085.** A rota e a cota leem o `ai_usage_log`: sem a 085 aplicada na mesma promoção
  (`RUN_DB_MIGRATIONS=true`), `POST /nutrition/meals/parse` e `GET /nutrition/meals/parse/quota` respondem 503
  `ai_usage_table_missing`; o resto do diário não é afetado.
- **Infra.** Uma env nova na `musclefit-workout-ai` (`OPENAI_NUTRITION_PARSE_MODEL = var.openai_model_nutrition_parse`,
  default `gpt-5.4-mini`), mais um comentário em `lambda_async_invoke_config.tf`: o plano esperado é `0 to add, 1 to
  change, 0 to destroy` (`aws_lambda_function.musclefit_workout_ai`, bloco `environment`); o resultado do job
  `terraform` e o smoke das imagens (`ci-docker-images`) ficam no PR. A role de deploy já cobre o
  `lambda:UpdateFunctionConfiguration` em `musclefit-*` (`infra/iam-policies/consolidacao/
  musclefit-gha-deploy-v2-compute.json`, statement `LambdaMusclefit`) e a de plan lê `lambda:GetFunction*`: sem
  exceção de IAM. A role da workout-ai já tem a Data API, a tabela de jobs e o secret da OpenAI; a api-core já invoca
  a função. O deploy aplica o Terraform antes do código. Sem a env (apply atrasado ou falho), o código usa o mesmo
  `gpt-5.4-mini`.
- **Comportamento.** Rota nova na api-core e job novo na `musclefit-workout-ai` (concorrência reservada da Lambda
  inalterada); `POST /nutrition/meals` aceita o item estimado e os campos da leitura (refeição manual e item do
  catálogo exatamente como antes; o `INSERT` grava duas colunas a mais em `meals`, NULL na refeição manual, e uma
  em `meal_items`). Corpo novo com texto livre passa pelo WAF da borda: o Core Rule Set está em Count em produção
  desde o SEG-05 (11/10), e o bloqueio entra com o #579 (na develop desde 18/10) na próxima promoção, com
  `SizeRestrictions_BODY` e `NoUserAgent_HEADER` continuando em Count. O DEV não tem WAF: o smoke da promoção
  confere o `wafResponseCode` (abaixo).
- **Tempo.** A OpenAI tem até 50 s (com as retentativas dentro do orçamento) e a busca, até 2 consultas FULLTEXT
  por item pela Data API (medido no eval: 0,9 a 4,5 s por leitura inteira, com até 5 itens). No pior caso (30 itens,
  OpenAI no limite) a Lambda de 60 s pode morrer: o job vira `worker_lost` só depois de 120 s, a tela desiste em
  90 s e a linha do `ai_usage_log` fica `pending` até o próximo fechamento de qualquer leitura (o sweep); a cota não
  muda (conta toda tentativa), só a auditoria atrasa.
- **Custo.** Medido: ≈ US$ 0,002 por leitura (~1,2 mil tokens de entrada a US$ 0,75/M + ~260 de saída a US$ 4,50/M).
  Teto realista por leitura: uma refeição no teto de itens (~1,5 mil de entrada + ~2,6 mil de saída) ≈ US$ 0,013; com
  a cota cheia todos os dias (30 × 30), ≈ US$ 11,7 por usuário por mês. Teto absoluto (laço degenerado até o
  `max_output_tokens` de 16.384): ≈ US$ 0,075 por leitura, ≈ US$ 2,25 por usuário por dia com a cota cheia. Uso
  esperado (1–3 usuários, poucas leituras por dia): centavos por mês. O custo de cada leitura fica gravado no
  `ai_usage_log` (L5a).
- **Reversão.** Reverter o código remove a rota e o job (a env sobrando não faz nada). Refeições `ai_text` já
  gravadas continuam legíveis (o diário do L4 lê o snapshot), mas a que tiver item estimado (`food_id` NULL) não se
  edita mais pelo L4 (`MealItemIn.food_id` volta a ser obrigatório: o `PUT` responde 422); só se apaga. Reverter a
  env é outro apply.
- **Promoção.** Entra na próxima promoção com a 084 (M85) e a 085 (L5a), que este lote não altera. Roteiro de
  verificação depois do deploy (só o usuário dono da conta, com a cota dele):
  1. `POST /nutrition/meals/parse` com uma refeição curta → 202; `GET /jobs/{id}` → `done` com os itens;
  2. `SELECT status, model, tokens_in, tokens_out, cost_usd FROM ai_usage_log ORDER BY id DESC LIMIT 1` →
     `succeeded`, `gpt-5.4-mini-*`, tokens e `cost_usd` não nulos;
  3. salvar a refeição pela tela → `meals.source = ai_text` e, se houver item fora da TACO, `meal_items.food_id`
     NULL com `nature = ESTIMATED`;
  4. `GET /nutrition/meals/parse/quota` → `used` subiu;
  5. no log `/aws/apigateway/musclefit-api-access`, o `wafResponseCode` dos dois POSTs (leitura e gravação) não é
     `BLOCK`.

## 56. Nutrição — Lote L6: IA na foto do rótulo nutricional (2026-10-18)

Plano: [`docs/nutricao/PLANO_NUTRICAO_2026-10-14.md`](../nutricao/PLANO_NUTRICAO_2026-10-14.md) §4, §8.1, §8.3,
§9 (b), §10, §12, §13 e §15 (D-E). Tabela regra → dono: §3.7 (linha "Foto do rótulo"). Consome a base do L5a (§54):
cota, feature, job type e o `ai_usage_log`. Roda em paralelo com o L5b (§55, texto da refeição). Sem migração: o
alimento vai para `food_items` (080) com `source = label_photo`, código que o catálogo já tinha.

**Dono novo.** `domain/nutrition/label_photo.py` (Python puro, sem relógio):
- upload: `LABEL_PHOTO_MAX_BYTES` = 1,5 MiB (`LABEL_PHOTO_MAX_MIB` × `BYTES_PER_MIB`), só JPEG
  (`LABEL_PHOTO_CONTENT_TYPE`, `LABEL_PHOTO_ACCEPTED_CONTENT_TYPES`), uma imagem por leitura
  (`LABEL_PHOTO_IMAGES_PER_READING`), `upload_error` (`label_photo_empty`/`_too_large`/`_unsupported_type`) e o
  transporte em base64 sem padding (`decode_upload`, `LABEL_PHOTO_MAX_BASE64_CHARS` = o tamanho do limite em base64,
  `BASE64_BLOCK_BYTES`/`BASE64_BLOCK_CHARS`, `label_photo_invalid_encoding`);
- armazenamento: `LABEL_PHOTO_S3_PREFIX` = `nutrition-labels`, `LABEL_PHOTO_EXTENSION`, `label_photo_key(user_id,
  uuid_hex)`, `is_label_photo_key_of`, `LABEL_PHOTO_RETENTION_DAYS` = 30 (espelho da regra de lifecycle, paridade);
- redução no navegador: `RESIZE_MAX_EDGE_PX` = 1280, `RESIZE_JPEG_QUALITY_PERCENT` = 85 → `RESIZE_JPEG_QUALITY`
  (sobre `body_composition.PERCENT`; a fração em Python é reservada pela guarda `limiar_fadiga`);
- leitura: `LABEL_IMAGE_DETAIL` = `high`, `LABEL_NUTRIENT_FIELDS` (os de `catalog.PER_100_FIELDS` + `ENERGY_KJ_FIELD`,
  iguais aos nutrientes de `labels.RDC429_FIELDS`, paridade), `LabelLegibility`, `PREFILL_DECIMALS`, a exibição das
  observações da IA (`clean_ai_notes`, `AI_NOTES_MAX`, `AI_NOTE_MAX_CHARS`) e a revisão (`review_reading` →
  `LabelReview`).
Defaults de desenho do plano (§8.1, §9 b, §9), sem fonte bibliográfica. `meal_math` ganhou
`per_reference_from_serving` (por 100 a partir de uma porção, a inversa de `label_consistency`); `catalog`,
`USER_ENTRY_SOURCES` (`user`, `label_photo`) e `user_entry_source`; `labels`, os textos dos códigos `label_*`,
`LABEL_LEGIBILITY_LABELS` e `LABEL_REGULATION_REFERENCE` (a norma citada no prompt). O bloco `nutrition` do JSON gerado
ganhou `label_photo` (`max_bytes`, `content_type`, `resize_max_edge_px`, `resize_quality`, `retention_days`,
`food_source`), `label_legibilities` e `label_legibility_labels`.

**Contrato.**
- `POST /nutrition/foods/label-photo` (api-core), corpo JSON `LabelPhotoUploadIn` `{image_base64}` (a foto já reduzida
  pela tela, base64 sem padding; com padding também é aceito): `decode_upload` (413 antes de decodificar se o texto
  passar do limite; 400 `label_photo_invalid_encoding`/`label_photo_empty`) → `upload_error` (413/415, o tipo pela
  assinatura dos bytes, `gyms.sniff_image_content_type`) — nada disso gasta cota — →
  `nutrition_ai_quota.open_usage(feature=LABEL_PHOTO, prompt_version, image_count=1)` (429 `ai_quota_exceeded`) → S3
  (`nutrition_label_storage.put_label_photo`, `Cache-Control: private, no-store`) →
  `async_jobs.create_job(LABEL_PHOTO_JOB_TYPE, ...)` → `link_job` → `invoke_async("PLAN_GENERATOR_FUNCTION_NAME",
  {job_id, job_kind, user_id, ai_usage_id, s3_key})` → **202** `{job_id, status, poll_timeout_seconds}` (o prazo do
  worker perdido do M73 para o job type: 240 s). Falha no S3, no DynamoDB ou no invoke fecha a linha como `failed`
  (`label_photo_storage_failed`/`label_photo_dispatch_failed`) e responde 503; no invoke, o job também vira `failed`.
- `GET /nutrition/foods/label-photo/usage`: `usage_today(...).to_dict()` ("x de 10 hoje"; o número vem da API).
- Job (`musclefit-plan-generator`, `job_kind` = `ai_usage.LABEL_PHOTO_JOB_TYPE`; `async_jobs.JOB_TYPE_LAMBDA` com a
  chave `ai_usage.LABEL_PHOTO_JOB_TYPE`): chave do próprio usuário (senão `label_photo_key_invalid`, sem ler o S3) →
  foto (`label_photo_not_found` quando o objeto não existe; em produção a role do worker não tem `s3:ListBucket`, então
  o S3 responde 403 e o código fica `label_photo_storage_failed` — o job lê a foto segundos depois do upload, e o caso
  não ocorre no fluxo normal) → `nutrition_label_ai.read_label` (uma chamada, timeout de 90 s < 180 s da Lambda,
  paridade) → Pydantic → `review_reading` → `job_result` (`food` pré-preenchido na escala de `PREFILL_DECIMALS`,
  `per_serving`, `errors`, `warnings` com o texto do dono, `ai_notes`, `atwater`, `label_check`; ~3 KB no DynamoDB).
  `finish_usage` em TODOS os caminhos: sucesso (modelo e tokens), falha da OpenAI (código `OPENAI_*`), forma inválida
  (com os tokens gastos), foto ausente, erro inesperado.
- Cadastro: o mesmo `POST /nutrition/foods`, agora com `FoodItemCreateIn` (o corpo de `FoodItemIn` + `source`); só as
  fontes de `USER_ENTRY_SOURCES` (outra → 400 `invalid_food_source`); o PUT continua `FoodItemIn` (`source` → 422).

**Decisões** (do lote, com as revisões do guardião e do impacto em produção).
1. **A IA só transcreve; o motor decide.** Nenhuma conta nem conferência no prompt: kJ → kcal (`meal_math.kj_to_kcal`),
   por 100 a partir da porção (`meal_math.per_reference_from_serving`), Atwater e porção × por 100
   (`meal_math.label_consistency`) são do motor, dentro de `review_reading`. O prompt interpola a norma, os campos e
   unidades da RDC 429, a base (100 g/ml) e os códigos de base e de legibilidade (os de `not_significant` vêm do enum
   do schema, `catalog.PER_100_FIELDS`); o teste
   renderiza o prompt com marcadores no lugar desses valores e exige zero dígito no texto. (A v1 pedia à IA uma
   auto-checagem "porção ≈ por 100 × porção / 100, tolerância de 10%": saiu na v2, revisão do guardião — era a conta do
   `label_consistency` no prompt, com um segundo aviso que podia divergir do motor.)
2. **"Não contém quantidade significativa"**: a IA lista o código em `not_significant` e deixa o valor em null; o
   zero é do motor, com o aviso `label_not_significant:<campo>`, sem sobrescrever valor impresso.
3. **D-E confirmada** no eval pago de 2026-10-18 (gpt-5.4-mini, snapshot `gpt-5.4-mini-2026-03-17`): `text.format`
   `json_schema` strict com `input_image` funcionou em 36 de 36 chamadas (prompts v1 e v2), sem recusa nem JSON
   inválido. `openai_client.call_openai_structured_vision` (nova; `call_openai_structured` intacta) aceita
   `response_format=json_object` como alternativa, com a validação Pydantic do worker valendo nos dois modos.
4. **Corpo em base64 sem padding, não multipart** (revisão do impacto em produção). O CRS do WAF bloqueia
   XSS/LFI/RFI/SSRF no corpo desde o #579 e lê o corpo como texto (com URL/HTML decode); os bytes comprimidos de um JPEG
   num multipart podem casar uma assinatura por acaso, de forma determinística (a mesma foto, o mesmo 403 sem CORS), e
   o DEV não tem WAF para mostrar. O alfabeto `[A-Za-z0-9+/]` não tem `<`, `:`, `.`, `=`, aspas, `&` nem `%`, então não
   forma as assinaturas; o padding (`=`) fica de fora. Premissa: vale se nenhuma sub-regra do CRS decodificar base64
   (`BASE64_DECODE`), o que a AWS não publica (as transformações das regras gerenciadas não são documentadas); não há
   sub-regra conhecida que faça isso, mas é inferência até a observação pós-promoção. Custo: o corpo cresce um terço
   (até ~2 MB; com `binary_media_types = ["*/*"]` o evento da Lambda leva o corpo reencodado, ~2,8 MB, abaixo dos 6 MB
   da Lambda e dos 10 MB do API Gateway). `SizeRestrictions_BODY` segue em Count.
5. **Upload só depois da redução.** A tela SEMPRE reencoda a foto por canvas (`resizeImageForUpload`, maior borda e
   qualidade do dono, orientação do EXIF aplicada na decodificação; sem EXIF/XMP na saída); se a redução falhar, nada é
   enviado — nunca o arquivo cru como alternativa. A api-core aceita só JPEG.
6. **413/415/400 antes da cota**: erro de entrada não gasta tentativa. A foto ilegível é `succeeded` na auditoria (a IA
   respondeu e cobrou) e conta na cota, como toda tentativa (§54 decisão 1).
7. **A chave da foto não vai para `food_items.label_s3_key`** (a coluna da 080 fica NULL): a foto expira em 30 dias e o
   alimento não depende dela; a proveniência é `source = label_photo`. Sem mudança de SQL.
8. **Rótulo antigo (RDC 360, só por porção)**: o por 100 sai da porção pelo motor, com o aviso
   `label_derived_from_serving:<campo>` em cada campo.
9. **Teto de saída** `LLM_OUTPUT_OBSERVED_MAX_TOKENS["label_photo"]` = 256 (maior `output_tokens` do eval), com
   paridade `AiFeature.LABEL_PHOTO` × chave do teto; o teto efetivo é o piso de 16.384. O teste
   `test_toda_feature_com_eval_tem_teto` passou a descontar as features de `AiFeature` (eval próprio da nutrição).
10. **Polling**: a tela espera até o `poll_timeout_seconds` da resposta (o do worker perdido), sem número próprio.

**Guarda.** Padrões novos, os dois nascem vazios:
- `foto_rotulo_nutricao` (repo inteiro): constantes do dono redefinidas, o prefixo `nutrition-labels` em literal (com ou
  sem a barra; o id da regra de lifecycle não casa) e 1,5 MiB à mão (`1572864`, `1.5 * 1024`, `1024 * 1024 * 1.5`,
  `1536 * 1024`);
- `foto_rotulo_numeros` (escopo de nutrição + `frontend/src/lib/image-resize.ts`): 1280, qualidade (`quality`/
  `qualidade` com fração ou inteiro), `toBlob(…, 0.85)`, `0.85`/`.85` solto, `maxEdge:`, `"image/jpeg"` em literal e
  `detail` da imagem em literal (`detail: "high"`, `ImageInput(…, "high")`).
Os arquivos novos estão no `ESCOPO_NUTRICAO` (`test_arquivos_da_foto_do_rotulo_estao_no_escopo_de_nutricao`). Nenhuma
allowlist cresceu. Fora da guarda (o `.tf` não é varrido): lifecycle, IAM e modelo, cobertos por testes de paridade que
leem `infra/s3_uploads.tf` e `infra/lambda_ai_generators.tf`.

**Eval pago** (`tests/eval/test_nutrition_label_eval.py`, critério `criterios_llm.avaliar_rotulo`, sem tocar em
`FORA_DO_ESCOPO`; casos `tests/eval/casos/nutrition_label.json`; imagens SINTÉTICAS em
`tests/eval/imagens/nutrition_labels/`, geradas por `tests/eval/gerar_imagens_rotulo.py` com a fonte Inter, OFL 1.1, e
números/marcas fictícios — nenhuma foto de rótulo real baixada). 8 casos (biscoito RDC 429, bebida por 100 ml, iogurte
com rotação/perspectiva/desfoque, granola no modelo antigo só por porção e kcal = kJ, arroz com "não contém
quantidades significativas", queijo com nome/marca/código de barras, foto borrada ilegível e a foto de ~1,5 MiB):
**v1, 2 rodadas: 16/16; v2 (a do PR), 2 rodadas: 16/16**. Por chamada (v2): 2.016 a 4.349 tokens de entrada (a
imagem já somada), 218 a 256 de saída, US$ 0,0026 a 0,0043 (teto, sem desconto de cache), latência de 1,2 a 2,9 s; a
foto de 1,54 MB levou 2,4 s e 2,1 s sem reduzir e 1,6 s e 2,6 s reduzida como a tela reduz (aceite do plano: < 30 s).
Custo das 4 rodadas (36 chamadas): ≈ US$ 0,11.

**Impacto em produção.**
- **Terraform** — o que este PR muda: 1 a adicionar e 2 a alterar, nada destruído:
  - `aws_iam_role_policy.musclefit_ai_generators_nutrition_labels` (nova): `s3:GetObject` só em
    `musclefit-uploads/nutrition-labels/*`, na role `musclefit-ai-generators-exec` (o nome não colide com as 3 inline
    de hoje: `-data-api`, `-exercise-card-drafts`, `-rag`);
  - `aws_s3_bucket_lifecycle_configuration.musclefit_uploads` (in-place): + regra `nutrition-labels-expire-30d`, a
    ÚLTIMA da lista; as duas de hoje (`exercise-card-drafts-expire-7d`, `versoes-antigas-90d`, conferidas em produção,
    só leitura) ficam com o mesmo conteúdo;
  - `aws_lambda_function.musclefit_plan_generator` (in-place): + `OPENAI_NUTRITION_VISION_MODEL` (variável
    `openai_model_nutrition_vision` em `lambda_ai_generators.tf`, junto dos outros modelos da plan-generator, default
    igual ao do código).
  O `terraform plan` do PR roda contra o state de produção e mostra também o que já está na develop e ainda não foi
  promovido: o `aws_wafv2_web_acl.api` e o alarme `waf_api_blocked` do #579 e a env `OPENAI_NUTRITION_PARSE_MODEL` da
  `musclefit-workout-ai` do L5b (#581); só as 3 mudanças acima são do L6. A política da api-core já cobre
  `PutObject` em `musclefit-uploads/*`. Um comentário de `api_gateway_borda.tf` mudou (sem efeito no plan).
- **Permissões conferidas** (`iam simulate-principal-policy`, só leitura, 2026-10-18): a `musclefit-gha-deploy` tem
  `s3:PutLifecycleConfiguration`/`GetLifecycleConfiguration` no bucket, `iam:PutRolePolicy`/`GetRolePolicy`/
  `DeleteRolePolicy`/`ListRolePolicies`/`GetRole` na role e `lambda:UpdateFunctionConfiguration`/`GetFunction`/
  `GetFunctionConfiguration`/`ListVersionsByFunction` na função; a `musclefit-gha-plan` lê os três. Sem exceção de IAM.
  (`GetFunctionCodeSigningConfig` dá `implicitDeny` na plan-generator, como já hoje, e os applies passam.)
- **WAF.** Corpo em base64 sem padding (decisão 4): o alfabeto não forma as assinaturas do CRS em bloqueio, na premissa
  de que nenhuma sub-regra decodifica base64 (não publicado pela AWS; inferência, não medição); `SizeRestrictions_BODY`
  conta (não bloqueia) o corpo acima de 8 KB. Medição opcional antes da promoção, só com aval do usuário (é tráfego para
  produção): com o CRS de produção ainda em Count, 5 a 10 corpos JSON com fotos reais em
  `POST https://api.musclefit.com.br/nutrition/foods/label-photo` sem cookie (a rota ainda não existe lá: 401/404, nada
  gravado) e `CountedRequests` por `ManagedRuleGroupRule` igual a zero. Os uploads multipart de foto/logo de `gyms.py`/`users.py` continuam com o
  risco herdado do #579 (fora deste lote): decisão do Tech Lead antes da promoção (medir, exceção no WAF ou base64
  como aqui). Observar após a promoção: `BlockedRequests` por sub-regra e o access log com
  `wafResponseCode = WAF_BLOCK` em `POST /nutrition/foods/label-photo` (esperado: zero). O DEV não tem WAF.
- **Imagens e cold start.** `ci-docker-images` dispara (o PR toca `infra/lambda_*.tf`) e confere o import nas imagens
  reais. `async_jobs` passa a importar `app.domain.nutrition.ai_usage` (contrato do L5a), o que carrega o pacote de
  nutrição também na workout-ai e na cardio-agent: ~130–200 ms de import a mais medidos localmente; conferir o
  `Init Duration` das linhas REPORT antes e depois (só leitura). Se pesar, o `__init__` do pacote pode ficar preguiçoso
  (PEP 562) num lote próprio.
- **Custo.** ≈ US$ 0,003 por leitura com o gpt-5.4-mini; no teto da cota (10/dia) ≈ US$ 0,9–1,3 por usuário por mês;
  no uso esperado (1–3 usuários, poucas fotos por semana) centavos por mês. S3: fotos de ~100–500 KB por 30 dias.
- **Retenção.** A foto expira pela regra de 30 dias; a exclusão física leva de 31 a 32 dias (o lifecycle é assíncrono e
  arredonda para a meia-noite UTC; a versão não corrente some 1 dia depois). A foto não é lida depois do job (a chave
  não é gravada): encurtar a regra ou apagar a foto ao fim do job fica para decisão do usuário (apagar exige
  `s3:DeleteObject` no prefixo para o worker, ou o delete na api-core). Na OpenAI, `store=false` impede guardar a
  resposta para consulta, mas não a retenção da OpenAI para monitoramento de abuso (até 30 dias, salvo contrato ZDR); a
  foto vai à OpenAI.
- **Promoção.** Vai na próxima promoção, junto com a 084 (M85) e a 085 (L5a), com `RUN_DB_MIGRATIONS=true`: o L6 não
  tem migração, mas depende da 085 (sem ela a rota responde 503 `ai_usage_table_missing`). O `terraform apply` do
  deploy aplica as 3 mudanças acima. Smoke da promoção, com critério de aceite:
  - `GET /nutrition/foods/label-photo/usage` responde 200 (404 seria o router caído em silêncio no `try/except` do
    `main.py`);
  - um upload real pela câmera do celular termina com o job `done`, a linha do `ai_usage_log` `succeeded` com tokens e
    custo, e o objeto em `nutrition-labels/<uid>/`;
  - `s3api get-bucket-lifecycle-configuration` com 3 regras, `iam get-role-policy` com a policy nova e
    `lambda get-function-configuration` com a env nova (só leitura);
  - zero `WAF_BLOCK` nesse path no access log.
- **Reversão.** Reverter o código é seguro (nada lê as fotos depois do job; os alimentos `label_photo` são linhas
  comuns do catálogo). A regra de lifecycle FICA num rollback de código, até o prefixo esvaziar: sem ela as fotos já
  gravadas nunca expiram (a `versoes-antigas-90d` só atua em versão não corrente). A policy de leitura do worker é
  inócua e pode sair junto com o código.

## 57. Núcleo do Modo Treino empacotado para dois clientes: celular e relógio (Evolução ZEPP, PR B, 2026-10-18)

Plano: Evolução ZEPP, §3 "PR B" e risco R5. O PR A não precisou de seção; a §58 é do PR C. Nenhuma regra nova: o PR move código,
e o único número que mudou de lugar (as reps do aquecimento genérico) foi para o dono.

**O espelho C7 passa a ter dois clientes.** A conta de cargas da sessão (`workout-mode-load-suggestions.ts`, espelho de
`domain/session_loads.py`, paridade `session-loads.parity.json`) e o resto da lógica pura do Modo Treino (fila, troca de
equipamento, sub-etapas do avançado, descanso, rótulo da próxima série, `set_type`, catálogo de técnicas, defaults de
prescrição e as constantes geradas) são reexportados por `frontend/src/lib/workout-mode-core/index.ts` e empacotados
por `frontend/scripts/build-workout-core.mjs` (esbuild, ESM, ES2020, sem externals) em
`frontend/dist/workout-mode-core/workout-mode-core.js` + `meta.json` (commit, sha256, bytes). Essa saída não vai para o
git. O celular importa os módulos-fonte; o relógio (Zepp OS) importa o bundle. São o MESMO código, então o espelho continua
um só, com a mesma paridade:
- `frontend/src/lib/workout-mode-core.test.ts` compila o bundle em memória. Confere que o texto não cita `window`,
  `document`, `localStorage`, `fetch`, `Intl` nem `process`. Roda `session-loads.parity.json` IMPORTANDO o bundle e
  valida o golden `workout-mode-core.golden.json` (array `[{name, fn, input, expected}]`) no bundle e na fonte.
- O mesmo golden roda no relógio e no `node --test` do repositório Zepp. Ele só se regenera com `UPDATE_GOLDEN=1`,
  nunca sozinho no CI.
- Persistência (`workout-mode-storage.ts`, `window.localStorage`) e IA (`workout-mode-ai.ts`, `apiFetch` + polling)
  saíram de `workout-mode-types.ts` e ficam fora do núcleo: cada cliente tem as suas.
- Funções que recebem `Set`/callback ganharam adaptadores JSON no núcleo (`workout-mode-core/json-api.ts`:
  `buildExecutionQueueFromIds`, `reorderPendingBySkipByPlanItem`, `findPlanItemForSubstitutedExerciseFromIds`). Eles só
  montam o `Set`/callback e delegam, sem regra.

**Aquecimento genérico saiu do componente.** A regra "2 séries opcionais antes do 1º exercício de um grupo primário ainda
não ativado, só no modo normal, antes da 1ª série, reconciliada com os aquecimentos já gravados" morava em
`PlanExecutionScreen.tsx`, com `GENERIC_WARMUP_REPS = 15` escrito à mão. Agora mora em
`frontend/src/lib/workout-mode-generic-warmup.ts` (`collectActivatedMuscleGroups`, `needsGenericWarmup`,
`resolveGenericWarmupState`, `shouldShowGenericWarmup`, `genericWarmupLoads`, `genericWarmupSet`), que o núcleo
reexporta. As reps foram para o dono: `domain/session_loads.GENERIC_WARMUP_REPS` (ao lado de `WARMUP_LOAD_FACTORS`),
exportado como `session_loads.generic_warmup_reps` em `motor-constants.generated.json` pelo gerador
`api/tests/unit/test_m76_lote1b_espelhos_fe.py --gravar`. O valor continua 15. A tela só consome.

**Mudou uma constante C7 ou `GENERIC_WARMUP_REPS` no dono?** Rode o `--gravar` do gerador e, no mesmo PR, regenere
também o golden (`UPDATE_GOLDEN=1 npx vitest run src/lib/workout-mode-core.test.ts`) e revise o diff. O golden congela
saídas que derivam dessas constantes. O repositório Zepp também precisa do bundle e do golden novos.

**Guarda.** Nenhum padrão novo e a allowlist não mudou. O número que antes era escrito à mão no componente deixou de
existir no frontend.

**Impacto em produção.** Nenhuma mudança de comportamento no celular. Os testes do Modo Treino passam iguais antes e
depois. A paridade C7 roda contra o bundle, e o golden cobre fila com pulo, troca antes e depois da 1ª série,
aquecimento genérico e configurado, ajuste, rest-pause, drop-set e descanso por etapa, executado no bundle e na fonte.
O bundle não entra no `next build` nem no deploy: é só artefato para o relógio.

### 57.1 PR B2 (2026-10-19): a orquestração da tela passa a morar no núcleo

O relógio (repositório Zepp, Z3, `data-widget/common/session-flow.js`) teve de copiar trechos de
`PlanExecutionScreen.tsx` que o núcleo não exportava; o guardião do Z3 marcou as cópias. O B2 dá dono a cada uma e o
núcleo passa a exportá-las. Nenhuma regra nova e nenhum valor mudou.

| O quê | Dono | Export do núcleo |
|---|---|---|
| Fonte do teto de RPE e do passo do par (prescrição congelada > `safety_overlay` do dia; par trocado lê `suggested-progression`; continuação sintética lê o que a API devolveu na troca) | `workout-mode-plan-flow.ts` (os valores são do servidor; a função só escolhe a fonte) | `resolveSessionPairLimits`, tipo `SessionPairLimits` |
| Item da ficha coberto por troca gravada em outro slot | `workout-mode-plan-flow.ts` | `findPlanItemsCoveredByAnotherSlot` (`Set`) e o adaptador JSON `findPlanItemsCoveredByAnotherSlotIds` (lista na ordem da ficha) |
| Âncora da próxima série (última série de trabalho) | `workout-mode-technique.ts` | `findAnchorWorkingSet`, tipo `TechniqueSetRow` |
| Alvos do par trocado em texto (nunca `null`) | `suggested-progression.ts` | `suggestedProgressionTargets`, tipo `SuggestedTargets` |
| Contadores do item (alvo de trabalho, aquecimento/ajuste do avançado, aplicações da técnica, progresso local) | `workout-mode-advanced-counters.ts` (novo) | `buildAdvancedExerciseCounters`, tipo `AdvancedCountersPlanExercise` |
| Piso de séries (`?? 1` / `\|\| 1` da tela) | `domain/prescription_defaults.MIN_SETS_COUNT` (já existia), exportado como `prescription.min_sets_count` | `MIN_SETS_COUNT` |
| Valor inicial do registro da série sem NENHUMA referência (`?? 10` reps e `?? 20` kg da tela e do treino livre) | `domain/prescription_defaults.SET_LOGGER_FALLBACK_REPS` / `SET_LOGGER_FALLBACK_LOAD_KG` (novos), exportados como `prescription.set_logger_fallback_reps` / `set_logger_fallback_load_kg` | `SET_LOGGER_FALLBACK_REPS`, `SET_LOGGER_FALLBACK_LOAD_KG` |

`?? 10`/`?? 20` não eram o `DEFAULT_REPS_TARGET` ("8-12", texto de faixa) nem o `time_estimate.DEFAULT_ESTIMATED_REPS`
(premissa da estimativa de tempo): são só o ponto de partida do campo, que o aluno ajusta, e por isso ganharam nomes
próprios no dono de defaults. `PlanExecutionScreen.tsx` e `FreeformScreen.tsx` passam a ler o espelho.

**Ficaram como UI, sem dono de motor.** O passo do RPE no campo (0,5, mínimo 0, em `SetLoggerCard.tsx`) é a
granularidade do campo de entrada: nenhum motor arredonda ou valida RPE em meios pontos (o teto vem do servidor e
`clampRpeSuggestion` só restringe a sugestão). O ajuste de ±15 s do `RestTimer.tsx` só empurra o cronômetro na tela;
não muda a prescrição, o que é gravado nem a estimativa de tempo. Vale para os dois aparelhos: o relógio pode ter granularidade de campo e ajuste de cronômetro próprios (o que cabe na tela dele) sem que isso seja cópia de regra.

**Golden também como módulo JS.** `build-workout-core.mjs` grava `dist/workout-mode-core/workout-mode-core.golden.js`
(`export default [...]`, o mesmo array do `workout-mode-core.golden.json`) ao lado do bundle, porque o build do Zepp OS
não importa `.json`; `meta.json` ganha `golden_sha256` (hash do `.golden.js`, não do `.json`) e `golden_cases`. O contrato com o repositório Zepp mudou só por adição. O vitest confere que o módulo gerado é o
mesmo array. O golden ganhou casos de contadores, cobertura, teto/passo do par, âncora e alvos do par (81 casos).

**Guarda.** Padrões novos `fallback_registro_serie` (piso de séries `sets_count ?? 1`/`|| 1`/`or 1`, `parseNumericTarget(...) ?? N`
e `SET_LOGGER_FALLBACK_* = N` fora do dono) e `fallback_registro_serie_cadeia` (só no Modo Treino: `reps ?? 10`, `load_kg ?? 20`
no fim da cadeia), os dois com allowlist vazia: as 7 cópias saíram no mesmo PR.

**Impacto em produção.** Nenhuma mudança de comportamento no celular. `workout-mode-advanced-counters.test.ts`
compara `buildAdvancedExerciseCounters` com a montagem antiga da tela (cópia literal) numa grade de 576 combinações;
os valores do espelho são os mesmos literais (1, 10, 20, com teste); o golden antigo não mudou (só casos novos).

### 57.2 PR B3 (2026-10-19): as últimas cópias do relógio passam ao núcleo

O guardião do Z3 deixou três pendências no `session-flow.js` do relógio: o fluxo/sub-etapa do avançado e o incremento do
progresso local ainda espelhavam a tela, a linha "Última" usava outro seletor (`findAnchorWorkingSet`) porque o do celular
não estava no núcleo, e o piso `1` da continuação sintética continuava escrito à mão. O B3 dá dono a cada uma. Nenhuma
regra nova e nenhum valor mudou.

| O quê | Dono | Export do núcleo |
|---|---|---|
| Item com fluxo avançado (modo avançado + técnica, aquecimento ou ajuste configurado) | `workout-mode-advanced-stage.ts` (novo) | `hasAdvancedFlow({advancedMode, planExercise})`, tipo `AdvancedFlowPlanExercise` |
| Sub-etapa exibida (fluxo avançado, ou técnica como sub-etapa nos dois modos; a sub-etapa em si é `resolveAdvancedSubStage`) | `workout-mode-advanced-stage.ts` | `advancedStageOf({advancedMode, planExercise, counters})` |
| Progresso efetivo de aquecimento/ajuste (maior entre o local e o gravado; `buildAdvancedExerciseCounters` passa a usá-lo e só limita ao alvo) e o incremento do progresso local ao concluir/pular (`+1` no efetivo; técnica soma `count`) | `workout-mode-advanced-stage.ts` | `effectiveAdvancedStageDone({localProgress, loggedSets})`, `advanceLocalStageProgress({existing, kind, effectiveDone, count?})`, tipo `AdvancedLocalStageKind` |
| Aplicações que "Encerrar técnica" marca como puladas (`total - index + 1`, sem piso: a sub-etapa sempre tem `index <= total`) | `workout-mode-advanced-stage.ts` | `remainingTechniqueApplications(stage)` |
| Séries de trabalho da sessão anterior (linha "Última"/"1ª série - ..."): só trabalho, sem aplicação de técnica, por `set_number` | `workout-mode-previous-session-sets.ts` (saiu de `workout-mode-previous-session.ts`, que o reexporta; o resto daquele módulo formata texto e fica fora do núcleo) | `selectPreviousSessionWorkingSets(response)`, tipos `LastSessionSetsResponse`, `PreviousSessionSet` |
| Piso de séries da continuação sintética da troca de equipamento (`Math.max(1, ...)` no fluxo e na tela) | `domain/prescription_defaults.MIN_SETS_COUNT` (já existia); aplicado só em `buildSyntheticContinuation`, a tela passa o que falta sem piso | `MIN_SETS_COUNT` (B2) e `buildSyntheticContinuation` |

Todas recebem e devolvem só JSON: nenhum adaptador novo em `json-api.ts`. `PlanExecutionScreen.tsx` e `FreeformScreen.tsx`
passam a consumir esses módulos. O golden ganhou 19 casos (100 no total). O piso da continuação passa a ser o MESMO
`MIN_SETS_COUNT` da prescrição: se o dono mudar esse valor, a continuação da troca de equipamento acompanha (o teste de
equivalência, que compara com o `1` antigo, quebra de propósito como alarme).

**Guarda.** O padrão `fallback_registro_serie` ganhou os ramos `max(N, <séries/alvo/restantes>)`, a ordem invertida
`max(<séries/restantes>, N)` e `<restantes/alvo> ?? N`/`|| N`, que travam o piso escrito à mão de volta
(`Math.max(1, remainingSets)`); `Math.max(1, lista.length)` (divisor da barra de progresso) não casa. Ficam de fora,
conscientemente: nome sem sets/target/remaining (`Math.max(1, faltam)`), expressão entre parênteses no argumento e o
ternário `x < 1 ? 1 : x`. A allowlist continua vazia.

**Impacto em produção.** Nenhuma mudança de comportamento no celular. `workout-mode-advanced-stage.test.ts` compara as
funções novas com a montagem antiga da tela (cópia literal) em todas as combinações de uma grade: 100 para fluxo/sub-etapa,
16 para o progresso efetivo, 576 para o incremento e 36 para "Encerrar técnica"; o teste do B2
(`workout-mode-advanced-counters.test.ts`, 576 combinações) segura a troca interna dos contadores para `effectiveAdvancedStageDone`. O piso da continuação é conferido contra
`Math.max(1, Math.max(1, x))` (o antigo da tela mais o do fluxo) para faltas de -2 a 5. `MIN_SETS_COUNT` continua 1, com
teste. O seletor da sessão anterior é a mesma função, reexportada, e os 23 testes dele não mudaram. O golden antigo não
mudou, só ganhou casos.

### 57.3 PR B4 (2026-10-19): o modo do treino sai da ficha, sem escolha "normal/avançado"

Decisão do usuário (2026-10-19): ninguém escolhe "normal" ou "avançado" antes de começar. Se a ficha tem qualquer
configuração avançada num exercício (aquecimento, série de ajuste ou técnica avançada), o treino é avançado; senão,
normal. Vale para o celular e para o relógio, com uma regra só, no núcleo.

| O quê | Dono | Export do núcleo |
|---|---|---|
| Item com configuração avançada: `advanced_technique` não-nulo, `warmup_reps` não-nulo ou `feeler_set_reps` não-nulo (os campos que montam as sub-etapas). O resíduo de técnica removida pela segurança (`advanced_technique` NULL com `advanced_technique_applications`/`duration_seconds` preenchidos) não conta; pico de contração sozinho não abre sub-etapa e também não conta | `workout-mode-advanced-stage.ts` | `exerciseHasAdvancedConfig(planExercise)`; `hasAdvancedFlow` passa a ser `advancedMode && exerciseHasAdvancedConfig(...)` |
| Ficha com configuração avançada (algum item) e o modo da sessão | `workout-mode-advanced-stage.ts` | `planHasAdvancedConfig(planExercises)`, `resolveWorkoutModeFromPlan(planExercises): 'advanced' \| 'normal'`, tipo `WorkoutMode` |

O critério é o mesmo `!= null` de `hasAdvancedFlow`, de propósito: com `> 0` uma ficha poderia sair "normal" e ainda
ter item com sub-etapa. Não há limiar numérico. Ficha vazia ou ausente é `'normal'`.

**Consumidores.** `ModeSelectScreen.tsx` resolve o modo ao iniciar a sessão (ficha existente: lê
`GET /workout-plans/{id}` antes do `POST /workout-sessions/start`; ficha gerada: a ficha gravada, depois da segurança)
e grava `advanced_mode` (`true`/`false`) na sessão local. `PlanExecutionScreen.tsx` só lê o valor salvo, então o modo
não muda no meio do treino. Retomada: com snapshot da mesma sessão vale o modo salvo, inclusive o escolhido à mão antes
do B4. Sem snapshot (storage perdido, sessão aberta no relógio), a mesma regra é aplicada à ficha. Se a ficha não
carregar, a retomada para com aviso e "Retomar" tenta de novo (retomar no normal em silêncio gravaria o modo errado até o fim).
O aviso "Treino avançado" no rascunho gerado é só prévia: quem decide é a ficha gravada. O card "Fazer treino avançado" e
a lista `has_advanced_technique=true` saíram da tela. O filtro do backend (`services.list_workout_plans`, que também
conta `peak_contraction_seconds`) é **legado sem consumidor** no frontend (só a rota e um teste de integração): não deve
ser consumido pelo celular nem pelo relógio (a guarda trava a volta no Modo Treino) e pode ser removido ou alinhado ao
núcleo numa PR futura. O relógio chama `resolveWorkoutModeFromPlan` do bundle.

**Guarda.** Padrão novo `modo_treino_pela_ficha` (só TS, escopo Modo Treino + `workout-mode-*`): a disjunção de duas ou
mais comparações `advanced_technique|warmup_reps|feeler_set_reps != null` na mesma linha fora do dono, e o filtro legado
`has_advanced_technique`. Uma comparação isolada não casa (os alvos de `buildAdvancedExerciseCounters` e o rótulo de reps
da tela). Escapam, conscientemente, a disjunção quebrada em várias linhas e a forma truthy. A allowlist nasce vazia.

**Impacto em produção.** Muda o comportamento no celular. A escolha some. Uma ficha com configuração avançada sempre
abre no avançado, com aquecimento/ajuste reais da ficha no lugar do aquecimento genérico. Uma ficha sem configuração
avançada abre no normal. O golden ganhou 11 casos. `workout-mode-advanced-stage.test.ts` prova que
`exerciseHasAdvancedConfig` é igual a `hasAdvancedFlow({advancedMode: true})` numa grade de 36 combinações.
Medição no DEV (dados de produção do usuário 1, só SELECT no MySQL local, 2026-10-19):

- 51 fichas (todas `done`; nenhuma `planned`): 25 cairiam no avançado e 26 no normal. As 38 fichas dos últimos 60 dias
  dividem-se em 25 avançadas e 13 normais. Pela regra nova e pelo filtro antigo do backend, o resultado é o mesmo
  (25). 1 item tem só pico de contração, e a ficha dele já é avançada por outro item.
- 8 itens têm resíduo de técnica removida. Nenhuma ficha é avançada só por resíduo, e nenhum item tem
  `warmup_reps = 0` ou `feeler_set_reps = 0`.
- O modo escolhido não fica gravado no servidor (só no `localStorage`), então a comparação usa um sinal indireto: série
  `feeler` gravada só existe no avançado. Das 25 sessões com ficha avançada (todas com ajuste configurado), 20 gravaram
  `feeler`, ou seja, o usuário escolheu o avançado. As 5 restantes, de 08/09 a 16/09, não gravaram `feeler`: foram
  feitas no normal ou antes de o ajuste existir no Modo Treino. Nas 26 sessões com ficha normal não houve nenhum `feeler`.
  Desde 17/09 a regra coincide em 100% com o que o usuário escolhia.
- Limite da medição: o DEV tem só a cópia do usuário 1 (`scripts/prod_to_dev.py --user-id 1`). Para outros usuários não
  há número: quem tem ficha avançada e escolhia o normal passa a ver aquecimento, ajuste e técnica. Na promoção, conferir
  com um SELECT agregado em produção (só leitura) quantas fichas `planned`/recentes por usuário caem no avançado, e
  fazer o smoke no celular: iniciar uma ficha avançada, uma normal e retomar uma sessão sem snapshot.

## 58. Gravação idempotente da sessão ao vivo por `client_key` (Evolução ZEPP, PR C, 2026-10-18)

Plano: Evolução ZEPP, §3 "PR C", §4 (contrato) e risco R6. Nenhuma regra nova de motor: o PR só evita gravar duas vezes
o mesmo exercício ou a mesma série quando a fila offline do relógio reenvia um pedido.

**Esquema (migração 087).** `exercises.client_key` e `exercise_sets.client_key` são `VARCHAR(64) ascii_bin NULL`, com
UNIQUE por pai real: `(activity_id, client_key)` e `(exercise_id, client_key)`. NULL não colide, então clientes sem a chave
(celular, importadores, `POST /activities`) não mudam. O tamanho (`CLIENT_KEY_MAX_LENGTH`) e o charset
(`CLIENT_KEY_PATTERN`) moram em `app/workout_sessions.py`. Um teste confere o tamanho contra a migração, que é autocontida.

**A resposta repetida consome os mesmos donos.** `add_session_set` passou a montar a leitura do motor em
`_evaluate_set`, usado pela série nova e pela repetição. A função chama `effective_intensity.set_intensity_reading`
(status e reps contra a ficha) e `engine_decision_context.next_set_load_for` (dono `domain/next_set_load.py`, com a
rampa `planned_ramp_rpe`). Na repetição, as entradas são as GRAVADAS da série (tipo, número, reps, carga, RPE, dor), não o
corpo reenviado. `rpe_target_effective`, `safety_event`, `technique_code` e `anchor_set_number` vêm da linha gravada.
A intensidade do par é relida de `effective_intensity.resolve_for_logged_exercise`, como na primeira chamada. Por isso,
se algo mudou entre o envio e a repetição, podem diferir da resposta original: o teto, `failure_allowed`,
`intensity_status`, `reps_position` e `next_set_load`. As causas possíveis são a recuperação ou o overlay do dia, a
prescrição/ficha, o passo ou a convenção do par, e séries de trabalho com número menor gravadas depois, fora de ordem
(a rampa). `safety_event` é sempre o gravado e, nesse caso, pode não bater com o `intensity_status` relido. Gravar o status
para evitar isso abriria um segundo caminho de regra. Se for preciso coerência estrita, o caminho é o próprio
`effective_intensity` expor uma leitura a partir do `rpe_target_effective` gravado, como mudança no dono.
A repetição não grava nada: nem a série, nem os agregados (`_refresh_*`), nem `log_event` de clamp ou de segurança.

**Corrida.** Se dois envios da mesma chave chegam juntos, o INSERT do segundo bate na UNIQUE (`is_duplicate_key_error`).
Uma leitura travante (`FOR UPDATE`, que enxerga a linha confirmada depois do snapshot do REPEATABLE READ) devolve o
registro do primeiro com `duplicate: true`. Qualquer outra violação, como `set_number` repetido com outra chave, sobe
como antes.

**Guarda.** Nenhum padrão novo e a allowlist não mudou.

**Impacto em produção.** Sem `client_key`, o SQL e a resposta são os de hoje mais dois campos: `client_key: null` e
`duplicate: false`. O INSERT sem chave nem cita a coluna nova. `GET /activities/{id}` (`SELECT *` das séries e `e.*` dos
exercícios) passa a trazer `client_key` (null sem chave). A 087 tem 4 statements no banco atual e 0 na reexecução, com
`ALGORITHM=INSTANT` no ADD COLUMN e `ALGORITHM=INPLACE, LOCK=NONE` no ADD UNIQUE: se o servidor não puder cumprir, falha
em vez de copiar a tabela. Com chave, o código precisa da 087. O `down` só roda depois de reverter o código, e o relógio só
manda a chave depois da promoção com a 087 aplicada.

## 59. Regras de carga da próxima série — motor de conjunto de regras (R1, 2026-10-20)

Decisões do usuário (2026-10-20): a regra de carga da próxima série (§36/§36.1) passa a ser um CONJUNTO DE REGRAS
cadastrável; as regras podem combinar posição da série, contexto de fadiga prévia, reps, RPE e a rampa planejada; a
magnitude pode ser em passos do equipamento ou em PERCENTUAL, mas o percentual só é aplicado dentro do dono,
arredondado ao passo do equipamento — tela e prompt recebem kg e texto prontos. Este PR (R1) entrega o motor, os
chamadores com o conjunto padrão e o instrumento de medição; a persistência do conjunto (migração 088 + loader) é o
R2 e a tela de cadastro (`/regras-carga`) é o R4. Nenhuma rota, migração ou tela aqui.

**Contrato (dono `domain/next_set_load.py`).** `RuleSet(reps_floor, reps_ceiling, rules, version=0, source="default")`
com `Rule(position, set_position, fatigue, reps, rpe, respect_planned_ramp, action, magnitude, reason_code=None)`;
`Condition(op, value, value_to)` com `ConditionOp` (`>`, `>=`, `<`, `<=`, `between`), valor numérico ou token
(`floor`/`ceiling` para reps = piso/teto do conjunto; `max` para RPE = teto efetivo "RPE máximo" da série,
`EffectiveIntensity.ceiling`); `Magnitude(kind, value)` com `steps` (n passos) ou `percent` (p). `SetFacts` carrega o
que a avaliação precisa (tipo, índice 1-based entre as `working` do exercício, faixa de fadiga, reps, RPE, teto, RPE
planejado, carga, passo, convenção, dor). `evaluate_ruleset(facts, ruleset) -> NextSetLoad | None`: (1) fora de escopo
(não-`working`, `assisted`/`bodyweight`, carga ≤ 0, reps inválidas) → None; (2) dor articular ou muscular acima de
`MUSCLE_PAIN_BLOCK_ABOVE` → REDUZIR 1 passo, `NEXT_SET_PAIN`, ANTES de qualquer regra e sem cadastro; (3) regras em
ordem, a PRIMEIRA que casa vence — posição (`any` ou a posição; índice desconhecido só casa `any`), fadiga (`any` ou a
faixa; faixa desconhecida só casa `any`), reps (tokens resolvidos pelo conjunto), RPE (sem condição = não exige RPE;
com condição e RPE ausente = não casa; `max` com teto ausente = não casa; `respect_planned_ramp` com rampa = o limiar
SUPERIOR vira `min(limiar, RPE planejado)` — se a regra casaria sem a rampa mas não com ela, fica marcada como barrada
pela rampa); (4) nenhuma casando → MANTER com `NEXT_SET_WITHIN_PLANNED_RAMP` se alguma foi barrada pela rampa, senão
`NEXT_SET_WITHIN_RULE`. Carga: manter = executada; `steps` n = executada ± n·passo arredondada ao passo; `percent` p =
executada × (1 ± p/100) arredondada ao passo, com ao menos um passo na direção (20 kg +5% → 21 → 20 no passo 2,5 →
vale 22,5); resultado ≤ 0 → direção sem carga. Regra sem `reason_code` → `NEXT_SET_RULE_MATCHED` ("Regra de carga
cadastrada") com `reason_label` = `describe_rule`. `NextSetLoad` ganhou, de forma aditiva, `rule_position`,
`rule_text`, `ruleset_version` (0 = padrão em código) e `magnitude` (`{kind, value}`).

**`DEFAULT_RULESET` = a regra atual.** Piso `NEXT_SET_DECREASE_BELOW_REPS` = 6, teto `NEXT_SET_INCREASE_MIN_REPS_LOW_RPE`
= 12 e três regras, todas "qualquer série · qualquer fadiga": (1) reps > `NEXT_SET_INCREASE_ABOVE_REPS` → +1 passo
(`NEXT_SET_REPS_ABOVE_LIMIT`); (2) reps ≥ 12 e RPE < `NEXT_SET_LOW_RPE_BELOW`, respeitando a rampa → +1 passo
(`NEXT_SET_LOW_RPE_AT_REPS`); (3) reps < 6 e RPE ≥ `NEXT_SET_FAILURE_RPE` → −1 passo (`NEXT_SET_FAILURE_BEFORE_MIN_REPS`).
`classify_next_set` e `suggest_next_set_load` continuam existindo e valem o mesmo (fachadas sobre o conjunto padrão;
`suggest_next_set_load` aceita `working_set_index`, `fatigue_band`, `rpe_ceiling` e `ruleset` opcionais). Um teste
compara `evaluate_ruleset(DEFAULT_RULESET)` com uma cópia literal da classificação anterior numa grade de reps 1..20 ×
RPE {sem, 6..10} × rampa {sem, 8} × dor {sem, articular, muscular 7}: idêntica.

**Fatos que os chamadores passam (só leitura; nenhuma regra).** `engine_decision_context.next_set_load_for` só monta os
`SetFacts` (`working_set_index`, `fatigue_band`, `rpe_ceiling`, `ruleset` novos e opcionais; sem índice explícito vale
a ordem no payload + 1). Salvamento da série (`workout_sessions._evaluate_set`, agora com `user_id` e `session_id`):
índice = `COUNT` das `working` de número menor + 1 (contado SEMPRE, não só com rampa), faixa de fadiga prévia do
exercício na sessão REAL pelo leitor `intelligence/session_fatigue_reader.load_session_prior_fatigue` (a faixa de
ANTES da 1ª série do exercício, a mesma chave da trilha; falha de leitura → None por `safe_read`), teto
`intensity.ceiling`, conjunto `DEFAULT_RULESET` até o loader do R2. IA por série (`dispatch_evaluate_set`):
`SetEvaluationIn` ganhou `session_id` e `exercise_id` (`exercises.id`) opcionais; o backend lê a faixa pelo mesmo leitor,
que já filtra `activities.user_id` (ownership); ausentes ou de outro usuário → None; teto = `(logged or intensity).ceiling`;
índice = `payload_working_set_index` + 1. O FE (`PlanExecutionScreen`, `FreeformScreen`) só passa `session_id`/`exercise_id`
no payload; `set-engine-status.NextSetLoad` ganhou os campos aditivos. O prompt `set-eval-v7` NÃO muda (snapshot
inalterado): recebe o mesmo `NextSetLoad`.

**Espelho do FE.** `export_for_frontend()` (bloco `next_set_load`) ganha `reason_rule_matched`, `rule_vocabulary`
(posições, faixas com os rótulos de `prior_fatigue` + "qualquer", operadores, tokens, ações com `DECISION_LABELS`,
tipos de magnitude), `rule_limits` e `default_ruleset` (`ruleset_to_dict`). A tela do R4 monta os Selects com isso e
nunca escreve valor nem rótulo; `describe_rule`/`describe_ruleset` são o texto pt-BR único das regras
("2ª série · músculo descansado · reps > teto (12) · RPE < máximo · respeita a rampa → Aumentar 25% (no passo do
equipamento)"). `validate_ruleset` devolve códigos (erros e avisos `WARN_*`; conjunto sem regra de redução é aviso) com
os limites nomeados `RULESET_MAX_RULES`, `MAGNITUDE_PERCENT_MIN/MAX`, `MAGNITUDE_STEPS_MIN/MAX`, `REPS_FLOOR_MIN`,
`REPS_CEILING_MAX`, `RULE_RPE_MIN/MAX`; `ruleset_from_dict`/`ruleset_to_dict` fazem o round-trip do JSON que a 088 vai
guardar.

**Guarda.** `carga_percentual` ganha o dono `next_set_load.py`, a linguagem `ts` e a forma `carga * (1 ± x / 100)`
(o percentual só vira kg em `_percent_load`); `carga_proxima_serie` ganha `DEFAULT_RULESET =` e
`RuleSet(reps_floor=<número>` (conjunto padrão só no dono); padrão NOVO `regra_carga_cadastrada` (condição de regra
avaliada fora do dono, `rule.reps.op ==`, `reps_condition … > 12`, `magnitude.value / 100`; escopo `api/app/` +
`frontend/src/`, excluindo a tela `/regras-carga` e `next-set-load-rules*.ts`, via o campo novo `excluir` do `Padrao`).
Allowlists: todas vazias (nada cresceu).

**Impacto em produção (medido no DEV com os dados do usuário 1).** Instrumento novo `api/scripts/replay_next_set_load.py`
(modelo: `replay_diff_progressao.py`; só leitura): roda o dono da base e o do branch sobre TODAS as séries `working`
com carga, com `SetFacts` reais (índice por `set_number` entre as `working` do exercício, faixa por
`load_session_prior_fatigue` por atividade, rampa de `prescription_json.rpe_target`/ficha, teto de
`prescription_json.effective_rpe_ceiling` — aproximação histórica: em produção o teto inclui o overlay de segurança
do dia, que não se reconstrói para o passado —, passo/convenção por `load_pair_step_context`; o índice conta todas as
`working`, com ou sem carga, como o salvamento), e imprime contagens por
direção, a tabela faixa de fadiga × posição e a lista de diferenças; `--ruleset-json` mede um conjunto cadastrado (base
do R5). Com `DEFAULT_RULESET`: 0 diferenças (os números ficam na descrição do PR). `replay_diff_progressao.py`: 0
diferenças (a prescrição da próxima sessão não foi tocada). Custo por série salva: a leitura da fadiga prévia da sessão
(uma consulta das séries da sessão + as duas do resolvedor de contribuições), dentro da mesma transação.

### 59.1 Lote R2 — conjunto gravado e versionado (migração 088) + rotas de cadastro (2026-10-20)

**Persistência (migração 088, `next_set_load_rule_sets`).** Uma tabela GLOBAL, uma linha por versão: `status`
`draft`/`published`, `version_number` (1, 2, … na publicação; rascunho NULL; UNIQUE), `reps_floor`/`reps_ceiling`,
`rules_json` (a lista de `rule_to_dict` do dono), `notes`, `engine_version` (`next_set_load.RULESET_ENGINE_VERSION`),
`created_by`/`published_by` (referências lógicas, sem FK), `published_at`, `superseded_at`. Colunas geradas
`current_published` = `IF(status = 'published' AND superseded_at IS NULL, 1, NULL)` e `current_draft` =
`IF(status = 'draft', 1, NULL)`, cada uma com UNIQUE: no máximo UM publicado vigente e UM rascunho (padrão 049/052/078).
Sem `effective_from`: vale ao publicar. Modelo 085/078 (`plan/plan_down/up/down`, `NextSetLoadRuleSetsError` antes
do 1º statement em forma divergente, dry-run aceita a tabela ausente, um statement por chamada, nenhum `:` em literal).

**Loader `app/next_set_load_rules.py` (I/O, nenhuma regra).** Pydantic de FORMA (`extra="forbid"`; vocabulário pelos
enums do dono: `SetPosition`, `FatigueCondition`, `ConditionOp`, `MagnitudeKind`; `position`/`text` aceitos e
ignorados; `reason_code` NÃO aceito — regra cadastrada usa `NEXT_SET_RULE_MATCHED` + `describe_rule`) ↔ dataclasses
do dono (`ruleset_from_dict`/`ruleset_to_dict`) ↔ linha. `load_published_ruleset(conn)` (salvamento da série, 1
SELECT por `current_published = 1` na conexão da série) e `load_published_ruleset_autocommit()` (IA por série, 1
leitura `fetch_rows`): sem linha, tabela ausente, falha de leitura, JSON ilegível ou conjunto que o dono recusa
(`ruleset_errors`) → `DEFAULT_RULESET` (logado), nunca exceção; sem cache (Lambdas multi-instância).
`save_draft` (rascunho único: cria ou substitui), `publish(conn, draft_id, expected_published_version, user_id)` (trava
a vigente com `FOR UPDATE`, compara a versão esperada — 0 = padrão em código —, revalida o rascunho no dono,
`version_number = max + 1`, encerra a vigente e promove o rascunho NA MESMA TRANSAÇÃO; divergência, `rowcount` 0 ou
UNIQUE → `RuleSetConflictError`), `list_versions`, `load_draft`.

**Dono (`domain/next_set_load.py`), aditivo.** `ruleset_uses_fatigue(ruleset)` (alguma regra com `fatigue != any`?);
`RULESET_SOURCE_DEFAULT/PUBLISHED/DRAFT`; `RULESET_ENGINE_VERSION`; código de validação `RULE_REASON_CODE_UNKNOWN`
(`reason_code` fora dos motivos de regra — os três do padrão e `NEXT_SET_RULE_MATCHED` — quebraria o rótulo; o
padrão continua válido). `export_for_frontend` não muda (JSON gerado intacto).

**Chamadores.** `workout_sessions._evaluate_set`: para série `working`, conjunto por `safe_read(load_published_ruleset,
conn)` (falha → padrão) e a faixa de fadiga SÓ é lida quando `ruleset_uses_fatigue(ruleset)`; série que não é de
trabalho não lê nada. `workout_ai_generation_core.dispatch_evaluate_set`: conjunto por
`safe_read(load_published_ruleset_autocommit)` e a faixa só com `ruleset_uses_fatigue`. `NextSetLoad.ruleset_version`
passa a ser a versão real (0 = padrão).

**Rotas `app/next_set_load_rules_admin.py`** (`prefix="/next-set-load-rules"`, api-core, catch-all `{proxy+}`;
registro em `main.py` no bloco try/except): `GET /published` (qualquer autenticado: conjunto em vigor + `text` por
regra, `summary` = `describe_ruleset`, `version`, `published_at`, `source: published|default`); o resto com
`require_admin`: `GET /draft`, `PUT /draft` (forma → 422 do Pydantic; `ruleset_errors` → 422 `invalid_ruleset` com
os `codes` do dono; avisos `WARN_*` voltam em `warnings` sem bloquear), `POST /publish` (`{draft_id,
expected_published_version}` → 409 `publish_conflict` com `current_version`; 404 `draft_not_found`; 422 se o dono
recusa o rascunho), `GET /versions`, `POST /describe` (textos, `errors`, `warnings`, sem gravar), `POST /simulate`
(`{ruleset | null, facts}` → `evaluate_ruleset(...).to_dict()` ou `null`; sem conjunto = o em vigor). Sem
`preview-impact` (R5).

**Guarda.** Nenhum padrão novo; as allowlists seguem vazias. Loader e rotas não avaliam condição nem aplicam
percentual (o `regra_carga_cadastrada` cobre `api/app/`).

**Impacto em produção (medido no DEV, usuário 1).** (1) 088: dry-run no `fitness` do DEV = **1 statement** (3
leituras, todas `information_schema`); aplicada num schema descartável próprio: 1 statement, **0 na reexecução**,
`GENERATION_EXPRESSION` do MySQL converge com a nossa, segundo rascunho e segundo vigente recusados (`Duplicate
entry`), `EXPLAIN` do vigente usa `uq_nslrs_current_published`; `down` inverso. (2) Sem linha na tabela o loader
devolve o padrão (inclusive com a tabela ausente no DEV): `replay_next_set_load.py --user-id 1` com a base = `api/`
da `develop` (92d4fed, R1) → **0 diferenças** nas 1.776 séries `working` com carga (77 atividades). (3) Statements por
série `working` em `_evaluate_set` (50 séries reais do usuário 1): R1 = 7 (3 da leitura de fadiga), R2 com o padrão
= **5** (+1 SELECT do conjunto, −3 da fadiga), ou seja −2 chamadas `ExecuteStatement` por série. IA por série com
`session_id`/`exercise_id`: R1 abria uma transação para a fadiga (Begin + 3 + Commit = 5 chamadas); R2 = 1 leitura
autocommit do conjunto.

### 59.2 Lote R4 — tela admin `/regras-carga` (2026-10-20)

**Tela** `frontend/src/app/(app)/regras-carga/` (`page.tsx` com `<AdminOnlyRoute>`, `RegrasCargaClient.tsx`,
`components/{RuleSetTable,ConditionField,GlobalParamsCard,SimulatePanel,VersionsList}.tsx`) e helper
`frontend/src/lib/next-set-load-rules.ts` (+ `-types.ts`, `.test.ts`); item "Regras de carga" no menu, só admin.
**Consumidor puro** das rotas do §59.1: nenhuma condição é avaliada e nenhum percentual vira kg no cliente.

- Rótulos e opções dos Selects (série, fadiga, operadores, tokens piso/teto/máximo, ações, tipo de magnitude) vêm de
  `next_set_load.rule_vocabulary` do JSON gerado; os limites dos campos e a validação de FORMA (vazio, fora de
  `rule_limits`, passos inteiros, teto de regras) de `next_set_load.rule_limits`; nenhum número escrito à mão.
- A semântica é do dono: o texto de cada regra, os erros e os avisos chegam por `POST /describe` (debounce) e o 422
  `invalid_ruleset` de `PUT /draft` (os `codes` viram mensagem legível, `Regra N: …` quando têm posição).
- "Simular uma série" chama `POST /simulate` com o rascunho do editor (ou `ruleset: null` = o vigente); o resultado
  exibido é o `NextSetLoad.to_dict()` do servidor (`nextSetLoadView`).
- "Restaurar padrão" carrega `default_ruleset` (JSON gerado) como rascunho SEM `reason_code` (o loader não o aceita):
  direção e carga iguais às do padrão, motivo passa a ser `NEXT_SET_RULE_MATCHED` com o texto de `describe_rule` e a
  versão vira ≥ 1 ao publicar. A tela avisa isso.
- Publicar exige o rascunho salvo e sem alterações; manda `expected_published_version` = versão vigente que a tela
  leu; 409 → aviso e recarga.

**Guarda.** Nenhum padrão novo; allowlists seguem vazias. A tela e o helper estão fora do escopo de
`regra_carga_cadastrada` (só montam Selects); `carga_percentual`, `percentual_de_carga` e `carga_proxima_serie`
continuam valendo neles (ts).

**Impacto em produção.** Só tela nova e menu; nada muda até um admin publicar. Publicar é GLOBAL: muda a carga
sugerida para a próxima série de todos os usuários a partir da próxima série salva.

### 59.3 Lote R3 — prompt `set-eval-v8` cita a regra que casou (2026-10-20)

**Prompt (`exercise_set_evaluation_ai.py`, `PROMPT_VERSION = "set-eval-v8"`).** `_format_next_set_section(next_set,
ruleset)` deixa de escrever a regra a partir das constantes `NEXT_SET_*` (a linha "Regra do motor: …" da v6/v7 saiu,
e o módulo não importa mais nenhuma delas). A seção da próxima série passa a ter, nesta ordem: direção e carga no
passo (como antes); "Motivo: `reason_label`" (como antes); o que casou, por `describe_matched_rule(next_set)` do dono
("Regra que casou: `rule_text`", "Nenhuma regra casou: manter a carga", ou nada quando o motivo é `REASON_PAIN`, que
vale antes das regras); "Antes de todas as regras: `describe_pain_rule()`" (dono: limiar `MUSCLE_PAIN_BLOCK_ABOVE` e
magnitude `ONE_STEP` por `_magnitude_text`); "Regras vigentes (vN[, conjunto padrão]), na ordem" seguida de
`describe_ruleset(ruleset)` (piso/teto e uma linha numerada por regra); a linha da rampa planejada quando há
`planned_rpe` (agora genérica: "regra que respeita a rampa usa o menor entre o limiar de RPE dela e o RPE planejado",
sem limiar escrito). Nenhum número nem magnitude do texto é escrito no módulo do prompt.

**Dono (`domain/next_set_load.py`), aditivo.** `describe_pain_rule()` e `describe_matched_rule(next_set)` (textos
pt-BR; a classificação "dor decidiu antes das regras" fica no dono, pelo `reason`).

**Conjunto.** `build_prompt(..., ruleset=None)` e `evaluate_set(..., ruleset=None)`; `None` = `DEFAULT_RULESET`.
`workout_ai_generation_core.dispatch_evaluate_set` repassa o MESMO conjunto que já avaliou a regra (o publicado de
`load_published_ruleset_autocommit`, padrão sem publicado ou na falha), então a lista do prompt e o `rule_text` saem
do mesmo `RuleSet`. "conjunto padrão" entra quando `ruleset.source == RULESET_SOURCE_DEFAULT`.

**Guarda.** Nenhum padrão novo; allowlists seguem vazias. `carga_proxima_serie`, `carga_percentual` e
`percentual_de_carga` continuam valendo para o texto do prompt (o texto da regra só vem do dono).

**Impacto em produção.** Só o texto da seção da próxima série muda (snapshot: 9 dos 10 casos do `set_evaluation`;
o caso sem regra aplicável, `apos_tecnica_avancada`, fica igual; nenhuma outra feature muda; `llm_request_snapshots.json`
igual). Tamanho: +103 a +166 caracteres por prompt com o conjunto padrão; pior caso medido com um conjunto sintético
de `RULESET_MAX_RULES` regras (todas as condições preenchidas) = +3.003 caracteres sobre o padrão (≈ 750 tokens de
entrada a mais por série). A carga e a categoria finais não mudam com nenhum conjunto: `bound_ai_load` e
`apply_engine_load_bound` forçam as da regra desde a v6; com o padrão o `NextSetLoad` é o mesmo. Eval pago
`-k set_evaluation` (gpt-5.4-mini, 2026-10-20): v7 = 10/10 sem problema; v8 (texto final) = 10/10 sem problema, mesma
categoria nos 10 casos. O eval só cobre o conjunto padrão (o harness chama `evaluate_set` sem `ruleset`); o ramo do
conjunto cadastrado tem só teste unitário de texto (`test_set_eval_v8_regra_que_casou.py`). Antes do 1º conjunto
publicado em produção, vale acrescentar 1 ou 2 casos de eval com um `RuleSet` cadastrado. Smoke da promoção:
`POST /exercises/evaluate-set` (job `workout-ai-evaluate-set`) em produção com `prompt_version=set-eval-v8` no evento `llm.usage` e
`next_set_load.ruleset_version=0`.

## 60. Medidas de série: repetições, tempo e máximo de reps — Lote T1 (dono + schema, 2026-10-20)

Pedido do usuário (2026-10-20): exercício **por tempo** (prancha, 3 séries de 1 minuto; o Modo Treino cronometra) e série
**"RM"** (máximo de repetições, podendo definir um tempo em que se pede o máximo; o Modo Treino registra quantas reps).
Escopo v1, em cinco PRs: T1 (este) cria **só o dono, o schema, o espelho do FE, a guarda e o mapa**; T2 valida o item do
Modelo/ficha e o tempo de treino; T3 grava a série e dá o cronômetro/registro no Modo Treino (celular); (a ordem final dos PRs
ficou: T3 = registro da série + motores neutros, §60.2; T4 = editor do Modelo, §60.3; T5 = telas do Modo Treino).
Relógio depois. Nenhum consumidor neste lote: nada no app muda de comportamento.

**Decisões do usuário / semântica (dono `domain/set_measures.py`).**

- Três medidas, `SetMeasure`: `reps` ("Repetições"), `duration` ("Tempo"), `max_reps` ("Máximo de repetições");
  sufixos curtos "reps" / "s" / "reps máx." (`MEASURE_SHORT_LABELS`). `DEFAULT_MEASURE = reps`.
- **Medida ausente = `reps`.** As colunas nascem `NOT NULL DEFAULT 'reps'`; toda linha anterior à 089 é `reps`;
  `normalize_measure(None)` devolve `reps`. **Nunca inferir a medida da série pelo item**: o relógio antigo e os
  importadores só mandam `reps`; uma série sem `measure` é de repetições mesmo que o item da ficha seja por tempo. Quem
  grava decide explicitamente (T3). `parse_measure` é a leitura estrita (desconhecido → None → 400 na rota, nunca `reps`
  em silêncio); `normalize_measure` é a leitura tolerante dos agregados.
- **Só `reps` é "baseada em reps"** (`is_rep_based`; SQL `sql_is_rep_based(col)` = `(col IS NULL OR col = 'reps')`,
  `sql_measure_expr(col)` = `COALESCE(col, 'reps')`). `duration` e `max_reps` ficam NEUTRAS em progressão,
  comparabilidade, e1RM, tonelagem e carga da próxima série, com o motivo estável `REASON_MEASURE_NOT_REP_BASED =
  "measure_not_rep_based"` e rótulo em `MEASURE_REASON_LABELS` (consumido no T3, §60.2). "Máximo de reps em 30 s" não é a
  mesma pergunta que "8-12 reps a RPE 8": comparar as duas daria progresso/regressão falsos.
- **Limites nomeados** (inclusivos): `DURATION_SECONDS_MIN/MAX` = 1..3600 s (alvo e registro da série por tempo);
  `TIME_WINDOW_SECONDS_MIN/MAX` = 5..600 s (janela do máximo); `MAX_REPS_MIN_REPS` = 1.
- **Forma do item** (`validate_item_measure(measure, duration_seconds_target, time_window_seconds, reps_target,
  has_advanced_block)` → códigos estáveis em `ITEM_VALIDATION_CODES`): `reps` proíbe os campos de tempo; `duration` exige
  `duration_seconds_target` nos limites, sem janela, sem `reps_target`, sem bloco avançado (técnica/aquecimento/ajuste);
  `max_reps` aceita janela opcional nos limites, sem alvo de tempo, sem `reps_target` ("máximo" não tem alvo), sem bloco
  avançado. Medida desconhecida → só `measure_unknown`.
- **Forma da série** (`validate_set_measure(measure, reps, duration_seconds)` → `SET_VALIDATION_CODES`): `reps` proíbe
  `duration_seconds` (reps pode faltar, como hoje); `duration` exige `duration_seconds` nos limites e `reps` NULL;
  `max_reps` exige `reps ≥ 1` e aceita `duration_seconds` (a janela usada) nos limites da janela.
- **Dose de recuperação** (`dose_reps_for_measure`): `reps`/`max_reps` → as reps gravadas; `duration` → None, e o
  consumidor cai no `DOSE_REPS_WHEN_MISSING` de `domain/e1rm.py` (8 reps, a mesma assunção de hoje para série sem reps).
  Decisão nomeada e **a revisar** com dados (uma prancha de 60 s pode merecer dose própria).
- **Tempo de treino** (`planned_set_execution_seconds`): `duration` → o alvo; `max_reps` com janela → a janela; senão
  None = a fórmula de reps do estimador (`periodization/time_estimate.py`, consumidor em T2).
- **Texto** (`format_set_measure`): "10 reps" / "60 s" / "15 reps em 30 s" (máximo sem janela: "15 reps"); sem o dado que
  a medida exige, `EMPTY_MEASURE_TEXT` ("—"). O espelho `formatSetMeasureText` produz o mesmo texto (paridade).

**Esquema (migração 089, um `ALTER TABLE … ADD COLUMN …, ALGORITHM=INSTANT` por coluna, 9 statements).**
`exercise_catalog.default_measure VARCHAR(16) NULL` (sugestão por exercício; NULL = sem sugestão);
`workout_plan_template_slot_items` e `workout_plan_exercises`: `measure VARCHAR(16) NOT NULL DEFAULT 'reps'`,
`duration_seconds_target INT NULL`, `time_window_seconds INT NULL`; `exercise_sets`: `measure VARCHAR(16) NOT NULL DEFAULT
'reps'`, `duration_seconds INT NULL` (tempo da série por tempo; janela usada em `max_reps`). VARCHAR livre como `set_type`
(sem ENUM a alterar por valor novo); sem FK, sem índice, sem backfill. Idempotente por coluna (`plan` lê
`information_schema`; convergido = 0 statements; tipo/nulabilidade/default divergente → `MedidasDeSerieError` antes do
1º statement); `plan_down` = `DROP COLUMN` de cada uma, em ordem inversa. A migração é autocontida; um teste confere que o
DEFAULT é o `DEFAULT_MEASURE` do dono e que o VARCHAR cabe o vocabulário.

**Espelho do FE.** Bloco `set_measures` de `motor-constants.generated.json` (`export_for_frontend`: medidas com `value`,
`label`, `short_label`, `rep_based`; `default_measure`; `values` por nome; `limits`; `empty_text`; motivo e rótulos) e
`set-measures.parity.json` (normalização e textos calculados pelo dono), ambos gerados por
`test_m76_lote1b_espelhos_fe.py --gravar`. `frontend/src/lib/set-measures.ts` só lê o JSON (`MEASURE_OPTIONS`,
`DEFAULT_MEASURE`, `MEASURE_LIMITS`, `isRepBased`, `normalizeMeasure`, `measureLabel`, `formatSetMeasureText`);
`set-measures.test.ts` roda a paridade.

**Guarda.** Padrão novo `medida_serie` (py + ts, allowlist vazia): comparação/atribuição com o literal (`== 'duration'`,
`=== 'max_reps'`, `es.measure = 'duration'`, `'max_reps' in …`, `measure == 'reps'`) fora dos donos
`domain/set_measures.py` e `set-measures.ts`. Não casa o acesso pelo Enum, as funções do dono nem chaves de JSON.

**Impacto em produção.** Nenhum consumidor grava ou lê as colunas; o JSON gerado só ganha o bloco novo. Todo INSERT das
4 tabelas lista colunas, então `measure` cai no DEFAULT `'reps'` e os tempos em NULL (celular, relógio, intervals-sync,
importadores, `prod_to_dev`). Dois contratos de leitura ganham chaves por `SELECT *`, de forma aditiva e ignorada pelos
clientes: as séries de `GET /activities/{id}` (`services.py`, `measure: "reps"`, `duration_seconds: null`) e os itens do
catálogo admin (`exercise_catalog_admin.py`, `default_measure: null`). Nenhum modelo tem `extra="forbid"`, o FE monta o
payload de edição da série por whitelist (`exercise-set-edit.ts`) e o prompt do resumo por IA usa campos explícitos (o
fingerprint `ai_summary_input_sha256` não muda). Dry-run da 089 contra o DEV com os dados de produção do usuário 1
(2026-10-20): 9 statements a aplicar
(1 + 3 + 3 + 2), 0 depois de convergido. Todas as linhas viram `reps`/NULL pelo DEFAULT: `exercise_sets` 2 031,
`workout_plan_exercises` 401, `workout_plan_template_slot_items` 556, `exercise_catalog` 118. `INNODB_TABLES.
TOTAL_ROW_VERSIONS` antes da 089: `workout_plan_exercises` 0, `workout_plan_template_slot_items` 1, `exercise_catalog` 5,
`exercise_sets` 1 — depois: 3, 4, 6 e 3, longe do teto de 64 do INSTANT (8.0.29+). `ADD COLUMN … NOT NULL DEFAULT` no fim
da tabela é INSTANT desde o MySQL 8.0.12 (default no metadado); as 4 tabelas são `ROW_FORMAT=Dynamic` sem índice
FULLTEXT (as duas restrições do INSTANT); DEV = MySQL 8.0.46, produção = Aurora MySQL 3.08.2 (8.0.39). As contagens,
o `ROW_FORMAT` e as versões de linha foram medidos no DEV (schema restaurado de produção), não na Aurora; o precedente
direto em produção é a 087 (INSTANT em `exercise_sets`, 20/10); em `exercise_catalog` os ADD COLUMN anteriores
(042/064) não declaravam `ALGORITHM`, e as outras duas tabelas só receberam colunas no baseline `.sql`. Nenhuma
migração cria FULLTEXT nessas tabelas e o teto de 64 exigiria 64 ALTERs INSTANT desde a última reconstrução. Se algum
ALTER não for elegível, o statement falha antes de as Lambdas receberem o código (o migrate roda primeiro), sem copiar a
tabela em silêncio, e a reexecução converge (cada ALTER já comitado é pulado pelo `plan`).

### 60.1 Lote T2 — Modelo → ficha → prescrição congelada (2026-10-20)

Primeiros consumidores do dono. Sem migração (usa as colunas da 089), sem tela nova (o editor da medida é o T4), prompt e
schema da Periodização inalterados. Nenhuma regra nova fora do dono. O dono ganhou: `ITEM_VALIDATION_MESSAGES` /
`item_validation_message(code)` (texto pt-BR de cada código de item, limites e rótulos interpolados das constantes);
`ADVANCED_BLOCK_PRESENCE_FIELDS` + `has_advanced_block(item)` (o que é "bloco avançado" para `validate_item_measure`:
presença de técnica, `warmup_reps`, `feeler_set_reps` ou pico — os companions tolerantes não contam sozinhos);
`declares_non_rep_measure(measure, duration_seconds_target, time_window_seconds)` e o código
`MEASURE_ONLY_ON_EQUIPMENT` (medida fora de `reps` num item que não é de equipamento; fora de `ITEM_VALIDATION_CODES`
porque não é forma do item). O espelho ganhou `plannedSetExecutionSeconds` (paridade em `set-measures.parity.json`,
seção `planned_execution`). O editor do T4 e o Modo Treino reutilizam esses critérios em vez de redefini-los.

| Consumidor | O que lê do dono | Comportamento |
|---|---|---|
| `models.py` (`WorkoutPlanTemplateSlotItemIn`, `WorkoutPlanExerciseIn`) | `parse_measure` (só na ficha) | 3 campos opcionais aditivos; na ficha, medida desconhecida = 422 e a válida é canonizada |
| `workout_plan_templates._validate_template_payload` | `validate_item_measure`, `has_advanced_block`, `declares_non_rep_measure`, `item_validation_message` | medida fora de `reps` (ou campo de tempo) só em `item_kind='equipment'` (`MEASURE_ONLY_ON_EQUIPMENT`); no item de equipamento, a 1ª violação do dono vira 422 "Slot 'X': <mensagem do dono>." |
| `template_versions.py` (`ITEM_FIELDS` + 3) | `normalize_measure` | SELECT/INSERT/cópia da reversão levam os 3; `measure` é gravada e comparada pela leitura do dono (ausente = `reps`): cliente antigo não abre versão nem grava NULL na coluna NOT NULL |
| `workout_plan_exercise_merge.ADVANCED_EXERCISE_FIELDS` + 3 | — | campo não enviado no PUT da ficha preserva o gravado |
| `services.py` (GET da ficha, `_replace_plan_exercises`, `_FREEZE_COLUMNS`) | `normalize_measure` | a ficha devolve e grava os 3 campos; o congelamento lê a medida da linha |
| `generation/mode_b.item_measure_and_reps` | `normalize_measure`, `is_rep_based` | a linha da ficha herda medida e alvos de tempo do item do Modelo; fora de `reps`, `reps_target` e `suggested_reps` = None (sem `DEFAULT_REPS_TARGET`) |
| `generation/freeze_mode_b.reps_fields_for_measure` | `is_rep_based` | mesma regra no snapshot do Modo B (a medida é a da LINHA da ficha) |
| `domain/prescription.build_prescription` | `normalize_measure` | chaves aditivas `measure`, `duration_seconds_target`, `time_window_seconds` (`SCHEMA_VERSION` igual) |
| `domain/training_history` (`PrescribedSpec`, `FrozenPrescription`, `FROZEN_SNAPSHOT_KEYS`) | `normalize_measure`, `is_rep_based` | `measure` lida da coluna `prescribed_measure` (quando a consulta a trouxer, T3) ou do snapshot (`$.measure`, ausente = reps); `to_dict` só a serializa fora de `reps` (evidência de `progression_decisions` igual para série de repetições) |
| `periodization/time_estimate.estimate_slot_time` (+ espelho `template-time-estimate.ts`) | `planned_set_execution_seconds` (`plannedSetExecutionSeconds`) | série de trabalho por tempo = o alvo; máximo com janela = a janela; senão a conta por reps de sempre (máximo sem janela estima as reps) |
| `domain/intensity_density.density_subindices` (+ espelho `intensity-density.ts`) | `is_rep_based` (`isRepBased`) | fora de `reps`, o subíndice de reps fica ausente (não entra como neutro) |
| `exercise_catalog_admin.py` / `equipment_exercises.py` | `parse_measure` | `default_measure` gravado só quando vem no payload (vazio = NULL, desconhecido = 400); a listagem de pares devolve `default_measure` (dica do editor no T4) |
| `periodization/validation._to_template_in` | `DEFAULT_MEASURE` | todo item gerado por IA nasce `reps`, sem campo de tempo |
| FE `workout-plan-template-form.ts`, `workout-plan-payload.ts`, tipos | — | hidratação e save carregam os 3 campos (salvar o Modelo pelo editor atual não apaga a medida); o payload da ficha repassa os campos como vieram (`undefined` some do JSON e o save preserva o gravado) |

Paridades regeneradas (`--gravar`): casos novos `duration`/`max_reps` em `template-time-estimate.parity.json` e
`intensity-density.parity.json`, `planned_execution` em `set-measures.parity.json`; os casos antigos não mudaram (só
inserções no JSON). Guarda `medida_serie` continua com allowlist vazia.

**Impacto em produção (medido no DEV com os dados de produção do usuário 1, 2026-10-20).** O DEV ainda não tem a 089:
as 3 tabelas foram copiadas para um schema de teste descartável (`t2_medidas_teste`, apagado no fim; nada escrito em
`fitness`), com os ALTERs da 089 aplicados nele. Itens de Modelo 556 / linhas de ficha 401, **100% `reps`**, 0 com tempo
ou janela; `exercise_catalog` 118 com `default_measure` NULL; 401 snapshots sem `$.measure` (= reps). Código de antes
(origin/develop) x depois, sobre esses dados: estimativa de tempo de 48 slots de 9 Modelos (vigentes) × 2 descansos
padrão (tela e complemento) = **0 diferenças**; Intensidade × Densidade de 299 itens = 0; linha da ficha do Modo B
(alvo e sugestão de reps, com e sem prescrição) de 204 itens de equipamento = 0; forma canônica das versões com o
payload do cliente antigo (sem os campos) = 0 slots abririam versão nova. Para quem treina só por repetições nada muda;
o snapshot congelado ganha 3 chaves aditivas. Dependência de deploy: o T2 lê/grava colunas da 089, então a promoção que
o levar precisa da 089 aplicada (#595 no mesmo lote; migrate com `RUN_DB_MIGRATIONS=true`, que roda antes das Lambdas;
sem ela os SELECTs da ficha, das versões do Modelo e dos pares dão "Unknown column"; conferir com `db-migrations.yml
action=status`). A medição acima é no DEV (pymysql, usuário 1): o smoke da promoção pela Data API real fecha a prova —
GET de uma ficha com `measure='reps'` e os outros dois `null`; PUT do Modelo sem mudança não cria versão; ficha do Modo B
congelada com `$.measure='reps'`; SELECT em produção com `measure <> 'reps'` = 0 nas duas tabelas (todos os usuários).
Pré-requisito do T4: o FE deste lote (que já carrega os 3 campos no save do Modelo) tem de estar em produção antes ou
junto do editor da medida — um bundle antigo salva o Modelo sem os campos e o item volta a `reps`.

### 60.2 Lote T3 — registro da série com medida e motores neutros (2026-10-20)

Backend do registro; telas do Modo Treino ficam no T5. Sem migração (colunas da 089). Nenhuma regra fora do dono: os
consumidores chamam `is_rep_based` / `sql_is_rep_based` / `dose_reps_for_measure` / `format_set_measure`. O dono ganhou
três fragmentos para quem agrega: `sql_if_rep_based(expr, column, otherwise)` (`CASE WHEN <reps> THEN expr ELSE 0 END`,
o termo reps × kg das somas inline), `sql_duration_seconds(column, seconds_column)` (segundos só da série `duration`; em
`max_reps` a coluna é a janela) e `has_non_rep_based(medidas)` (gatilho único de neutralidade). O motivo
`REASON_MEASURE_NOT_REP_BASED` é reexportado por `progression_engine` e `comparability` (não redefinido).

| Consumidor | O que lê do dono | Comportamento |
|---|---|---|
| `models.ExerciseSetIn`, `workout_sessions.SessionSetIn` (via `set_measure_input.py`) | `validate_set_measure`, `normalize_measure` | `measure`/`duration_seconds` opcionais; ausente = `reps` (nunca deduzida do item da ficha: o relógio antigo só manda reps); forma inválida = 422 com os códigos do dono. `ExerciseSetUpdateIn` valida DEPOIS da mescla (`update_exercise_log` -> 422 `{"codes": [...]}`) |
| INSERTs de `exercise_sets` (sessão ao vivo, `upsert_activity`, `PUT /exercises/logs/{id}`) e `_find_set_by_client_key` | `normalize_measure` | gravam as duas colunas; a repetição por `client_key` avalia a série com a medida GRAVADA; a edição preserva medida/duração omitidas (`PRESERVED_SET_FIELDS`) |
| `make_external_id`/`row_hash` (`services`) | — | as chaves novas com valor `None` ficam fora do texto hasheado: o mesmo treino reimportado sem `external_id` mantém o id (sem duplicata) |
| `_evaluate_set` → `effective_intensity.set_intensity_reading(measure=)` e `domain/next_set_load.evaluate_ruleset` (`SetFacts.measure`, repassado por `engine_decision_context.next_set_load_for`) | `is_rep_based` | fora de `reps`: `reps_position` e `next_set_load` = None (a regra é do dono R1/R2; `_evaluate_set` só deixa de ler o conjunto publicado, economia de I/O); status de esforço pelo RPE continua |
| avaliação da série por IA (`SetEvaluationIn.measure`, `workout_ai_generation_core._evaluate_set_reading` / `next_set_load_for`) | idem | mesma neutralidade; ausente = reps (cliente atual) |
| `services.compute_exercise_stats` | `is_rep_based` | `sets_count` conta tudo; `reps_total` inclui `max_reps`; `volume_kg` só séries de reps |
| `load_conventions.sql_counts_in_tonnage` / `sql_tonnage_sum` / `sql_tonnage_excluded_sets` (kwarg `measure`, default `es.measure`) | `sql_is_rep_based` | tonelagem = convenção que conta **e** série de reps; a série fora de reps entra nas "fora da tonelagem" |
| somas inline de reps × kg (`analytics` resumo por exercício, `cardio_context_service`, `volume_service` ×3, relatório: tonelagem por grupo e por equipamento) | `sql_if_rep_based` / `sql_is_rep_based` | só séries de reps somam carga; séries e reps continuam contando |
| `session_records` (`HISTORY_SQL`, `SETS_SQL`), e1RM "nesse equipamento" de `get_last_session_sets`, e1RM por grupo (`analytics._fetch_group_e1rm_rows`) e por sessão (`analytics` progressão do exercício), e1RM da sessão no `volume_service` | `sql_is_rep_based` / `is_rep_based` | série fora de reps fora de recorde, melhor carga e e1RM |
| `exposure_service` (`EXPOSURES_SQL`, `BATCH_EXPOSURES_SQL`, `build_exposures`, `_summarize_sets`, `to_summaries`), `exercise_performance._raw_exposure` | `normalize_measure`, `is_rep_based`, `has_non_rep_based`, `format_set_measure` | série com `measure`/`duration_seconds`/`measure_text`; agregados (reps, carga, RPE, e1RM, tonelagem) e `WorkingSet` só de reps; exposição marca `has_non_rep_sets` (contrato `ExposureOut`/`ExerciseSetOut` aditivo) |
| `comparability.classify_exercise_progression` / `compare_exposures` | `REASON_MEASURE_NOT_REP_BASED` | última ou anterior com `has_non_rep_sets` → `NOT_COMPARABLE` `[measure_not_rep_based]` (o par direto → `(None, [motivo])`); FE `STATUS_REASON_TEXTS.measure_not_rep_based` = `MEASURE_REASON_LABELS` do JSON gerado (paridade `test_todo_motivo_do_dono_tem_texto_no_frontend`) |
| `training_history.SetRecord` (+ `SESSIONS_SQL` lê `es.measure`, `es.duration_seconds` e `wpe.measure AS prescribed_measure`), `prescription_service._set_record` | `normalize_measure` | a série da progressão carrega a medida (`SetMeasure`) |
| `progression_engine.evaluate_track_session` (`working_has_non_rep_sets`) | `has_non_rep_based` | alguma `working` fora de reps → `NEUTRAL measure_not_rep_based`, **antes** da calibração e de qualquer RESET |
| `progression_evaluator.update_track_execution` e a evidência de carga pendente de `evaluate_session` | idem | nesse caso só `last_executed_at`: não calibra, não adota carga, não efetiva nem expira pendência (estado e evidência pela mesma porta) |
| `prescription_service.last_execution_from_logs` (via `list_exercise_logs`, que agora lê a medida) | `is_rep_based` | série fora de reps não é "última carga" da prescrição (como a aplicação de técnica) |
| `volume_service` (overview, dose do histórico, tendência), `recovery_read_model` (empacotado + janela) | `dose_reps_for_measure`, `is_rep_based`, `sql_if_rep_based` | a série conta 1 unidade em qualquer medida; reps da dose pela medida (`duration` → `DOSE_REPS_WHEN_MISSING`, a mesma assunção de hoje para série sem reps); `volume_kg` e queda de reps só de reps. No empacotado a medida vai VAZIA quando é reps (1 byte por série) |
| `session_fatigue_reader` | — | conta a série normalmente (fadiga prévia) |
| `GET /exercises/last-session-sets`, `GET /activities/{id}` (`services._attach_set_measure`) | `normalize_measure`, `format_set_measure` | cada série com `measure`, `duration_seconds` e `measure_text` ("58 s") |
| relatório Markdown (`daily_breakdown`, `markdown_renderer._equipment_reps`) | `sql_duration_seconds`, `format_set_measure` | por equipamento, a célula de reps ganha "+ 175 s" quando houve série por tempo |

Decisões declaradas (não são lacunas):

- **Agregados descritivos da sessão** — `best_weight_kg`/`reps_total` de `compute_exercise_stats`, de
  `analytics.exercise_progress` e de `adherence_service`/`engine_service` (executado × prescrito) continuam olhando
  todas as séries não preparatórias: descrevem o que foi feito, não são marca de carga. Recorde, melhor carga das
  exposições e e1RM filtram a medida (tabela acima).
- **Recorde por sessão × tela do exercício** — `session_records` descarta no SQL as séries fora de reps e compara as de
  reps (uma sessão só de prancha some do histórico de carga); a tela do exercício (`exposure_service`) marca
  `has_non_rep_sets` e fica NOT_COMPARABLE. Intencional: o recorde é marca de carga; o veredito da tela é "essas
  duas execuções respondem a mesma pergunta?". Rever no T5 se a sessão mista confundir.
- **`duration_seconds` solto sem `measure`** (cliente antigo) é ignorado em `SessionSetIn`/`ExerciseSetIn` (grava
  reps, sem duração), como antes do T3 — sem 422 que travaria a fila offline do relógio. Com `measure="reps"`
  explícito, continua 422 (`duration_not_allowed`).
- `load_conventions.sql_counts_in_tonnage`: a parte da convenção espelha `counts_in_tonnage`; a parte da medida, em
  Python, é `is_rep_based` aplicado por quem agrega (`exposure_service._summarize_sets`, `compute_exercise_stats`).

Guarda `medida_serie` continua com allowlist vazia (nenhuma comparação literal fora do dono). JSON gerado inalterado (o
bloco `set_measures` não mudou).

**Impacto em produção (medido no DEV com os dados de produção do usuário 1, 2026-10-20).** O DEV não tem a 089: todas as 80
tabelas de `fitness` foram copiadas para um schema descartável (`t3_medidas_teste`, apagado no fim; nada escrito em
`fitness`) com os 9 ALTERs da 089 aplicados. `exercise_sets` 2 031 linhas, **100% `reps`**, 0 com `duration_seconds`.
`replay_diff_progressao.py --user-id 1` com referência = `origin/develop` (d660e77) e atual = esta branch: 54 sessões,
**0 diferenças** (221 NEUTRAL / 115 QUALIFY / 79 RESET; decisões, fases, trilhas e próxima ficha iguais).
`replay_next_set_load.py --user-id 1`: 1 776 séries `working` com carga, **0 diferenças**. Com 100% `reps`, todo
predicado novo (`measure IS NULL OR measure = 'reps'`) é verdadeiro e todo `is_rep_based` devolve True: tonelagem,
`volume_kg`, e1RM, recordes, exposições e dose saem iguais por construção. Contratos: só chaves aditivas (`measure`,
`duration_seconds`, `measure_text` nas séries; `has_non_rep_sets` na exposição; contratos fechados do dashboard
atualizados). O hash de importação não muda para cliente antigo (teste). Dependência de deploy: lê e grava colunas da
089 (já na develop, #595) — a promoção precisa da 089 aplicada antes das Lambdas (migrate primeiro; sem ela, "Unknown
column" nos SELECTs/INSERTs de séries). Smoke pela Data API real na promoção: POST de série sem `measure` grava `reps`;
`SELECT measure, COUNT(*) FROM exercise_sets GROUP BY measure` = só `reps`.

### 60.3 Lote T4 — editor do Modelo e catálogo com a medida (2026-10-20)

Só frontend; nenhuma regra nova. O card de equipamento do Modelo (`EquipmentSeriesItemCard.tsx`) ganha o Select
"Medida" (`MEASURE_OPTIONS` do espelho) e os campos "Tempo por série (s)" (tempo) e "Janela (s, opcional)" (máximo),
com `min`/`max` de `MEASURE_LIMITS`. Fora de reps (`isRepBased`) o card esconde "Reps" e o bloco avançado (técnica,
aquecimento, ajuste, pico). A forma do estado mora em `lib/workout-plan-template-form.ts`:

| Função | O que faz | Lê do espelho |
|---|---|---|
| `measureChangePatch(measure)` | grava a medida e limpa o que ela não admite (campo de tempo da outra medida; fora de reps, `reps_target` e o bloco avançado inteiro), marcando `measure_touched` (só navegador, fora do payload) | `normalizeMeasure`, `isRepBased`, `MEASURE_VALUES` |
| `pairSelectionPatch(item, eq, ex, default_measure)` | escolher o par aplica o `default_measure` dele (sem sugestão = `DEFAULT_MEASURE`) enquanto o usuário não mexeu na medida; item hidratado conta como escolhido | `parseMeasure`, `DEFAULT_MEASURE` |
| `itemMeasureSummary(item)` | badge do card recolhido ("3 × 60 s", "3 × reps máx. em 30 s"); null em reps | `formatSetMeasureText`, rótulos curtos |
| `validateSlots` (forma) | tempo exige o alvo dentro dos limites; janela, se preenchida, dentro dos limites | `MEASURE_LIMITS` |

A mensagem definitiva continua sendo a do servidor (422 de `validate_item_measure`, exibido como os demais 422 do
editor). `/dominio-exercicios`: Select "Medida padrão" no drawer (vazio = NULL) e badge com `measureLabel` na listagem
quando a medida padrão não é reps. "Tempo total" do slot já vinha do espelho (`plannedSetExecutionSeconds`, §60.1).
O dono passa a exportar `ADVANCED_BLOCK_PRESENCE_FIELDS` no bloco `set_measures` (`--gravar`; espelho
`ADVANCED_BLOCK_PRESENCE_FIELDS` em `set-measures.ts`): um teste do editor exige que a troca para tempo/máximo limpe
cada um desses campos, então um campo novo no dono quebra o teste em vez de virar 422 silencioso. O drawer do catálogo
só manda `default_measure` no PUT quando o admin muda o Select (ausente = não muda).
Guarda `medida_serie` com allowlist vazia (comparações só por `MEASURE_VALUES`/`isRepBased`).

**Impacto em produção.** Modelos existentes (100% `reps`, §60.1): o card mostra "Repetições" e o save sai igual — teste
de round-trip (`workout-plan-template-form.test.ts`, "round-trip de item reps sem tocar a medida") prova payload idêntico
ao item que veio do GET. O drawer do catálogo passa a mandar `default_measure` no PUT (o valor carregado; NULL nos 118
de hoje). Depende do backend do T2 (#597) e da migração 089 em produção, na mesma promoção.

### 60.4 Lote T5 — Modo Treino (celular) e registro com séries por tempo e de máximo de reps (2026-10-20)

Só frontend; nenhuma regra numérica nova. O Modo Treino passa a executar as medidas do §60: série por tempo com
cronômetro regressivo até o alvo, série de máximo de reps com a janela regressiva (ou cronômetro livre) e reps
obrigatórias, carga opcional nas duas. A decisão do que a tela mostra mora no núcleo do Modo Treino (§57), módulo puro
`frontend/src/lib/workout-mode-measures.ts`, que só lê o espelho `set-measures.ts` (vocabulário, `isRepBased`,
`MEASURE_LIMITS`, `formatSetMeasureText`). Nenhuma comparação literal de medida (guarda `medida_serie`, allowlist vazia).

| Função (exportada pelo núcleo) | O que decide | Lê do espelho |
|---|---|---|
| `setMeasureOf(item)` | medida do item (prescrição congelada com `measure` > linha da ficha; ausente/desconhecida = reps) e alvos de tempo | `normalizeMeasure` |
| `measureLoggerDefaults({measure, durationSecondsTarget, timeWindowSeconds, repsTarget, lastSet, suggestedReps})` | campos e valores iniciais do card: reps (em reps, IA > última série > alvo > `SET_LOGGER_FALLBACK_REPS`, igual a antes; em tempo, nenhum; no máximo, IA > última série, nunca o alvo), segundos (última série > alvo), janela, `loadOptional`, `repsRequired`, `durationRequired` | `isRepBased`, `MEASURE_VALUES` |
| `executionTimerSpec({...})` | `none` (reps) / `countdown` (alvo do tempo ou janela do máximo) / `stopwatch` (sem alvo/janela) | idem |
| `measureTargetText` / `measureSetLabel` | "60 s", "reps máx. em 30 s"; "Série 1 de 3 · 60 s" (reps: o rótulo como veio). O editor do Modelo (`itemMeasureSummary`, §60.3) passa a usar o mesmo texto | `formatSetMeasureText`, rótulo curto |
| `setMeasurePayload(values)` | chaves de medida do POST da série e da avaliação por IA: `{}` em reps (payload idêntico ao de antes); fora de reps, `measure` explícita e `duration_seconds` (segundos executados; a janela no máximo) | `normalizeMeasure`, `isRepBased` |
| `isDurationSecondsValid(s)` | habilita "Salvar série" por tempo (a palavra final é o 422 do dono) | `MEASURE_LIMITS` |
| `isMaxRepsValid(reps)` | habilita "Salvar série" de máximo de reps (piso `MAX_REPS_MIN_REPS`); os campos de reps/segundos do card e do registro usam o mesmo piso/teto (`setEditFields` devolve `repsMin` e os limites de tempo × janela) | `MEASURE_LIMITS` |
| `exerciseHasAdvancedConfig` / `hasAdvancedFlow` / `resolveWorkoutModeFromPlan` | item fora de reps nunca é avançado, nem com resíduo de técnica/aquecimento/ajuste | `isRepBased` |
| `shouldShowGenericWarmup({..., measure})` | aquecimento genérico só em item de reps (`measure` ausente = reps) | `isRepBased` |
| `resolveNextSetPreview({..., measure})` | fora de reps, só o rótulo, `weightKg: null` (sem sugestão de carga; o servidor já devolve `next_set_load: null`) | `isRepBased` |
| `buildSyntheticContinuation` / `syntheticExtraToPlanExercise` | a continuação após troca de equipamento herda a medida e os alvos de tempo (campos copiados só quando presentes) | — |

Telas: `SetLoggerCard.tsx` (variantes por tempo e por máximo dentro do card padrão, com `ExecutionTimer.tsx`:
iniciar / pausar / retomar / encerrar antes, tempo = acumulado + `Date.now()` − início do trecho, recalculado a cada tick
e no `visibilitychange`; bipe + vibração de `timer-feedback.ts`, extraído do `RestTimer.tsx`); `PlanExecutionScreen.tsx`
(defaults, rótulo e POST pela medida; fora de reps a carga inicial é a da última série ou vazia); `FreeformScreen.tsx`
(seletor de medida com `MEASURE_OPTIONS`, travado depois da 1ª série); "Treino anterior"
(`workout-mode-previous-session.ts`: `measure_text` do servidor, espelho como fallback); `NextExercisePreviewCard.tsx`
(alvo da medida). Registro: `lib/exercise-set-edit.ts` faz o round-trip de `measure`/`duration_seconds` (sempre
explícitos no PUT, reps NULL preservado em série por tempo) e `/registro-exercicios/[activityId]` mostra "Tempo (s)"
fora de reps (limites do dono: tempo da série × janela do máximo), herda a medida ao adicionar série e resume a tabela
com o texto da medida.

Golden do núcleo: 111 → 138 casos (27 novos, só inserções: os casos antigos não mudaram). Bundle
`workout-mode-core.js`: 37 320 → 40 734 bytes (minificado).

**Impacto em produção.** Fichas e séries de hoje são 100% `reps` (§60.1/§60.2): `setMeasureOf` devolve `reps`, o card,
os defaults e o fluxo avançado são os de antes, e o POST da série sai byte a byte igual (teste
`workout-mode-measures.test.ts`, "o POST de uma série de reps é o MESMO objeto de antes"; golden "POST da série: reps não
ganha chave nenhuma"). Única mudança de contrato para série de reps: o PUT da edição do registro passa a mandar
`measure: "reps"` e `duration_seconds: null` explícitos (o T3 aceita, pela leitura do código: `ExerciseSetUpdateIn` não
valida o payload parcial, `update_exercise_log` valida a série mesclada e `validate_set_measure` em reps só recusa
`duration_seconds` presente; evita herdar a medida de outra série quando a tela renumera). Não há teste ponta a ponta do
PUT: o smoke da promoção edita uma série de reps em `/registro-exercicios/<id>` (200) e confere, só leitura, que a linha
segue `measure='reps'` e `duration_seconds IS NULL`. Depende do backend do T3 (#602) e da 089 na mesma promoção; a
ordem S3 × Lambdas é indiferente (nenhum modelo de série usa `extra="forbid"`, um backend sem T3 ignora as chaves
novas, e não há série fora de reps em produção). Relógio (repositório `musclefit-zepp`): ainda não lê a medida; um item
por tempo/máximo executado no relógio é gravado como série de **reps** (o servidor aceita série sem `measure`, sem
erro), o que é dado errado: entra em volume e comparabilidade como série de reps. Hoje há 0 itens fora de reps; depois
desta promoção (com o editor do T4) o usuário já pode criá-los, então até o Z-PR esses itens devem ser executados no
celular (ou o Z-PR sai na mesma janela). O Z-PR
futuro consome os exports novos do bundle (`setMeasureOf`, `measureLoggerDefaults`, `executionTimerSpec`,
`measureSetLabel`, `measureTargetText`, `setMeasurePayload`, `isDurationSecondsValid`, `isMaxRepsValid`, `formatSetMeasureText`,
`isRepBased`, `normalizeMeasure`), o `meta.json` e o golden novos (138 casos), e manda `measure`/`duration_seconds` no POST.

## 61. Edição de ficha não finalizada em `/planos-treino` (2026-10-24)

Pedido do usuário: numa ficha não finalizada, editar equipamento, séries, reps, carga, RPE, descanso, técnica avançada
e demais parâmetros de cada exercício; só o grupamento muscular fica travado. Ao trocar o equipamento, carga e
evolução vêm do histórico do novo equipamento.

| Regra | Dono | Consumidores | Proibido fora do dono |
|---|---|---|---|
| Ficha editável (`EDITABLE_PLAN_STATUSES = {"planned"}`; `done`/`completed`/`skipped` são finais), trava do grupamento (texto da linha salva e grupo PRIMARY do exercício novo), campos cuja mudança re-congela a linha salva (`REFREEZE_ON_CHANGE_FIELDS`: exercício, equipamento, técnica) | `api/app/workout_plan_edit_rules.py` (ciclo de vida da ficha, sem cálculo) | `services.update_workout_plan`/`_replace_plan_exercises` (409/422), `services.assert_plan_prescription_refreshable` (status), `get_workout_plan` (`editable`), tela da ficha (só lê `editable`) | status editável ou trava decididos na tela, no SQL ou na rota |
| Exercícios elegíveis para a troca numa linha salva (predicado único `is_same_primary_group_swap`: mesmo grupo PRIMARY, ou um dos lados sem grupo; lista `eligible_swap_exercise_ids`, `None` = sem restrição) | `api/app/workout_plan_edit_rules.py` (predicado e lista), lidos por `services.list_exercise_swap_options` → `GET /workout-plans/{id}/exercise-swap-options` | validação do save (`_assert_exercise_keeps_primary_group`, 422), autocomplete da linha salva em `ExercisesTable.tsx` (só filtra o catálogo pelos ids, via `frontend/src/lib/exercise-swap-options.ts`) | casar texto de grupo na tela; grupo do catálogo (`/mapping-canonical/options.primary_group`) como critério da troca |
| Grupo PRIMARY de um exercício | `progression_state.load_primary_groups` / `primary_groups_from_cursor` (mesma leitura e desempate) | trava do grupamento no save da ficha e lista de elegíveis para a troca | outra consulta de grupo primário no save ou na lista |
| Carga da linha na troca de equipamento | motor v2 do par (`suggested_load_kg` do snapshot recongelado), formatado por `workout_plan_ai.normalize_load_target` (mesmo da geração Modo B) via `services.sync_load_target_from_frozen_suggestion` | `workout_plan_equipment.substitute_equipment`; a tela só reflete a linha devolvida | carga do equipamento antigo herdada; carga calculada na tela |

Semântica nova (medida abaixo):
- PUT sem `exercises` não toca nos exercícios (antes, a ausência virava `[]` e apagava a lista); ficha finalizada
  recusa `exercises` com 409 `plan_finalized` e aceita o save só do cabeçalho (inclusive reabrir: status → planned).
- Linha salva que muda de exercício, equipamento ou técnica no PUT é re-congelada (`SOURCE_REFRESH`); séries, reps,
  carga e RPE escritos à mão continuam sem re-congelar (PR-11).
- Troca de equipamento (M8): vale em ficha `planned` de data passada (`require_future_date=False`); o refresh da ficha
  inteira continua exigindo `plan_date >= hoje`. A carga escrita na linha é descartada antes do recongelamento e volta
  como a sugestão do motor para o par novo (vazia sem histórico).

**Impacto em produção** (SELECT em 2026-10-24): 65 fichas — 61 `done`, 4 `planned` (ids 143–146, datas 15 a
19/10, todas com academia; 48 linhas, todas com grupo e exercício vinculado; o texto do grupo de todas bate com um
grupo PRIMARY do exercício, então a trava não recusa nenhum save de hoje). As 61 `done` passam a ter exercícios
somente leitura. As 4 `planned` hoje não tinham troca de equipamento nem refresh (data passada); com este PR a troca
fica disponível nelas, e o refresh continua indisponível. 2 exercícios do catálogo (115 "Elevação de Pernas na
Estação Vertical" e 137 "Prancha Isométrica") têm mais de um grupo PRIMARY: para eles a lista da tela (grupo do
catálogo) pode divergir do grupo do dono; o servidor decide (422 com mensagem). Reabrir uma ficha finalizada continua
possível por decisão (save só do cabeçalho com status → planned); a trava vale para os exercícios enquanto ela está
finalizada. A tela só omite `exercises` com `editable === false` explícito (backend anterior não manda o campo e
trataria a ausência como lista vazia). Prova do SQL novo no artefato real: integração MySQL (`ci-completo`) no último
push e smoke da promoção; a medição é de 2026-10-24 e deve ser refeita (SELECT das `planned`) se a promoção atrasar.

**Correção (2026-10-24, troca de exercício na linha salva):** a lista do autocomplete não abria com as opções do
mesmo grupamento. Causa: o Mantine Autocomplete filtra pelo valor do campo, e na linha salva o valor é o próprio nome
do exercício, então a lista mostrava só ele; além disso o grupo era casado por texto na tela, com o grupo do catálogo
(`emc.id ASC`) em vez do grupo do dono (maior peso, depois menor id). Agora o servidor devolve os ids elegíveis por
linha (mesma regra e mesmo predicado do 422), a tela só filtra por id e, ao focar ou clicar, mostra a lista inteira da
linha (filtra por texto só depois que o usuário digita). **Impacto em produção** (SELECT em 2026-10-24): 4 fichas
`planned` (143–146), 48 linhas, todas vinculadas a exercício ativo do catálogo (107 ativos) e com o nome igual ao do
catálogo. Antes, as 48 linhas abriam com 1 opção (o próprio exercício); agora abrem com 2 a 11 (mediana 8). 2 dos
107 exercícios têm dois PRIMARY com o mesmo peso (115: grupos 1 e 55; 137: grupos 1 e 55). Para o 137, o catálogo
dava Core (55) e o dono dá Abdômen (1). Em 2 das 48 linhas a lista antiga (texto do grupo) era diferente da que o
servidor aceita; agora são iguais por construção (teste de paridade). Nenhuma das 48 linhas usa 115 ou 137. A
validação do save não mudou: só ganhou o predicado nomeado. O endpoint novo é só leitura e roda na api-core (rota
catch-all, sem Terraform), com 5 SELECTs fixos (ficha, linhas, catálogo ativo, ids legados e grupos PRIMARY).
A tela não compara mais texto de grupo (`sameGroupName`/`lockedGroupOf` removidos); a volta é barrada na revisão.

## 62. Modo Intensivo para técnicas avançadas — PR 1, backend (2026-10-24)

Pedido do usuário: no FST-7 (e nas demais técnicas), registrar a série com um toque, RPE-alvo automático e ajuste
rápido de carga por fadiga, sem digitar carga, reps e RPE em descansos de 30 s. Decisões de 2026-10-24: o RPE-alvo da
técnica é 10 quando o teto efetivo do par libera a falha e 9 nos demais casos (inclusive teto desconhecido), sem
rebaixar pela recuperação; a série com reps pendentes é gravada com `reps` NULL e `reps_pending = 1` e NENHUM motor ou
leitura analítica a vê até a confirmação; o RPE prescrito nunca vai para `exercise_sets.rpe`.

| Regra | Dono | Consumidores | Proibido fora do dono |
|---|---|---|---|
| RPE-alvo do protocolo da técnica (`PROTOCOL_TARGET_RPE_WITH_FAILURE` = `FAILURE_RPE`, `PROTOCOL_TARGET_RPE_WITHOUT_FAILURE` = 9; `protocol_target_rpe(max_safe_rpe)`; congelado só restringe: `effective_protocol_target_rpe`); vocabulário `rpe_target_source` (`RPE_TARGET_SOURCE_PROTOCOL`) | `api/app/domain/training_safety.py` | `progression_engine.technique_parameters` (`target_rpe` em `advanced_technique_parameters`), `effective_intensity.protocol_target_for_logged_exercise` (teto do par ao vivo × congelado lido por `effective_intensity.frozen_protocol_target_rpe`), `POST .../sets` com `rpe_target_source='protocol'` grava em `rpe_target_effective`; a tela só lê `target_rpe` | 9/10 escrito na rota, no SQL, na tela ou no prompt; alvo do protocolo rebaixado pela recuperação; alvo prescrito gravado em `rpe` |
| Ajuste rápido de carga (`quick_load_adjust(current, step, action)`: `minus_step` = degrau abaixo na grade; `minus_5pct`/`minus_10pct` = `QUICK_ADJUST_FACTORS` arredondado PARA BAIXO ao passo; piso 0); vocabulário `load_adjust_source` (`LOAD_ADJUST_SOURCES` = protocol, fatigue, manual) | `api/app/domain/session_loads.py` | espelho do FE `mirrorQuickLoadAdjust` em `frontend/src/lib/workout-mode-load-suggestions.ts` (PR 2, §63) contra `frontend/src/lib/session-loads.parity.json` (`quick_load_adjust_cases`) e as constantes de `motor-constants.generated.json` (`session_loads.quick_adjust_*`, `load_adjust_sources`), gerados por `test_m76_lote1b_espelhos_fe.py --gravar`; validação de `load_adjust_source` no `POST .../sets` | fator, arredondamento ou piso escritos na tela; arredondar para cima |
| Série com reps pendentes fora dos motores (`sql_set_confirmed(column)` = `<col> = 0`; `is_pending_set`, `confirmed_sets`; `REPS_PENDING_COLUMN`) | `api/app/set_types.py` | TODA leitura de `exercise_sets` em `api/app` (volume, e1RM/recorde, comparabilidade, `technique_volume`/`technique_accounting`, fadiga, ativação, progressão, relatórios, dashboard, IA) e os agregados de `exercises` (`services.compute_exercise_stats`, `workout_sessions._refresh_exercise_aggregates`); exceções operacionais registradas na guarda | leitura de `exercise_sets` sem o predicado; `reps_pending` comparado à mão; contar a pendente pelo fallback de dose |
| Mescla da edição com o Modo Intensivo (`PATCHABLE_SET_FIELDS`, `merge_set_patch`, `resolve_reps_pending`, `SERVER_KEPT_SET_FIELDS`, `keeps_protocol_target`) | `api/app/exercise_set_update_merge.py` | `PATCH /workout-sessions/{id}/exercises/{ex}/sets/{n}`, `PUT /exercises/logs/{id}` (preserva carga planejada, origens, hora da conclusão, alvo do protocolo e a pendência) | pendência ou alvo do protocolo decididos na rota ou na tela |

**Guarda nova.** `api/tests/unit/test_guarda_series_pendentes.py` varre `api/app/**/*.py` pela AST: todo literal que LÊ
`exercise_sets` (`FROM`/`JOIN`, fora DELETE/INSERT/UPDATE) precisa interpolar `sql_set_confirmed` uma vez por
ocorrência. Exceções operacionais com motivo e contagem exata (a lista só encolhe): idempotência por `client_key`,
numeração/âncora da série nova e posição ordinal da rampa (`workout_sessions`), a leitura do próprio PATCH e a
edição do `PUT /exercises/logs` (que recria as séries e preserva a pendência). As telas que MOSTRAM a série pendente
(`GET /activities/{id}` e `GET /exercises/logs`) pedem `include_pending=True`; os consumidores analíticos das mesmas
funções (resumo por IA, progressão, geração de ficha) não. A guarda das fontes da verdade ganhou dois padrões que nascem com a allowlist vazia: `rpe_alvo_protocolo`
(`target_rpe`/`targetRpe`/`rpe_target_effective` atribuído ou comparado a 9/10 fora de `training_safety.py`) e
`serie_pendente_a_mao` (`reps_pending` comparado a 0/1 fora de `set_types.py`). Um teste fixa que só `main.py`
(`get_activity`, `get_exercise_logs`) passa `include_pending=True`.

**Posição ordinal x contagem.** A posição da série na rampa planejada (`_evaluate_set`, exceção da guarda) conta a
pendente: ela ocupa o seu lugar no bloco (a 5ª série do FST-7 é a 5ª mesmo com a 4ª pendente). Já as contagens
analíticas (`training_history.WORKING_SETS_COUNT_SQL`, volume, agregados) só contam as confirmadas. São perguntas
diferentes de propósito; a exceção ordinal tem contagem fixa na guarda.

**Script de replay.** `api/scripts/replay_next_set_load.py` (fora de `api/app`) também filtra as pendentes com o
predicado do dono; numa versão anterior à 092 (sem `sql_set_confirmed`) não existe série pendente e o filtro é neutro.

**Contrato da API.** `POST .../sets` aceita `reps_pending` (exige `reps` NULL e medida `reps`), `completed_at`
(aware -> hora de parede do usuário), `planned_weight_kg` (≥ 0), `load_adjust_source` e `rpe_target_source='protocol'`
(o servidor grava o alvo do dono; exige técnica e série não preparatória, senão 422). A resposta (nova e repetida por
`client_key`) traz `reps_pending`, `completed_at`, `planned_weight_kg`, `load_adjust_source`, `rpe_target_source`;
série pendente sai com `next_set_load` nulo. `PATCH .../sets/{n}` aceita `reps`, `weight_kg`, `rpe` (ao menos um):
informar `reps` confirma; `reps: null` em série de reps -> 422; treino finalizado -> 409; recalcula `safety_event` e os
agregados. `reps` NULL sem a marca continua aceita no POST (compatibilidade com `/registro-exercicios`).

**Impacto em produção** (SELECT em 2026-10-24): `exercise_sets` tem 2.350 linhas, 0 com `reps` NULL, `reps` já é
`int NULL` (001) e nenhuma das 5 colunas novas existe; `TOTAL_ROW_VERSIONS` = 3 (a 092 soma 5 INSTANT, longe do teto
de 64); MySQL 8.0.39. Toda linha existente recebe `reps_pending = 0` pelo DEFAULT e passa no predicado: volume,
e1RM, progressão, relatórios e agregados leem exatamente as mesmas linhas de hoje. `rpe_target_effective` das séries
antigas (654 preenchidas) não muda; o alvo do protocolo só é gravado com `rpe_target_source='protocol'`, que só o
Modo Intensivo (PR 2) envia. `advanced_technique_parameters` ganha `target_rpe` nas prescrições congeladas DAQUI em
diante (aditivo; snapshot antigo sem o campo -> o POST usa só o teto ao vivo). O PUT de `/registro-exercicios` passa a
citar as 5 colunas no INSERT (valores das séries gravadas; série nova -> 0/NULL, iguais ao DEFAULT). Prova do SQL novo
no artefato real: integração MySQL (`ci-completo`) no último push e smoke da promoção; antes da promoção,
`db-migrations.yml` com `action=status`/`dry-run` em produção (plano esperado: 5 ALTERs da 092, pela Data API real).
O deploy roda o migrate antes das Lambdas (`vars.RUN_DB_MIGRATIONS` = `true`, conferido em 2026-10-24): o código
novo LÊ `reps_pending` e não roda sem a 092.

**Rollback.** Primeiro as Lambdas (o código antigo ignora as colunas novas); só depois o `down` da 092, e apenas se
não houver `reps_pending = 1` gravado (o `DROP COLUMN` transformaria a pendente em série com `reps` NULL
"confirmada"). Rodar o `down` com o código novo no ar derruba as leituras de `exercise_sets` (coluna ausente).

## 63. Modo Intensivo para técnicas avançadas — PR 2, frontend FST-7 (2026-10-24)

Tela do Modo Intensivo no `/modo-treino` (briefing da técnica, série com um toque, descanso com ajuste rápido de
carga, revisão), com o FST-7 como referência. Nenhuma regra nova: a tela só consome os donos da §62.

| Regra | Dono | Consumidores | Proibido fora do dono |
|---|---|---|---|
| Ajuste rápido de carga (espelho) | `api/app/domain/session_loads.py` (`quick_load_adjust`) | `mirrorQuickLoadAdjust`/`quickAdjustFactor` em `frontend/src/lib/workout-mode-load-suggestions.ts` (ações, fatores e origens lidos de `motor-constants.generated.json`, bloco `session_loads`; paridade `it.each` sobre `quick_load_adjust_cases` de `session-loads.parity.json`); `quickAdjustPlannedKg`/`quickAdjustOptions` em `workout-mode-intensive.ts` convertem carga e passo para a unidade exibida (`weight-units.ts`, passo de `loadStepperStep` sobre `load_increment_kg` de `resolveSessionPairLimits`) antes do espelho e voltam para kg; os rótulos "−5%/−10%" saem dos fatores | fator, arredondamento, piso ou rótulo percentual escritos na tela |
| RPE-alvo do protocolo | `api/app/domain/training_safety.py` (`protocol_target_rpe`) | a tela LÊ `prescription.advanced_technique_parameters.target_rpe` (`resolveIntensiveProfile` -> `targetRpe`); ausente (snapshot antigo) mostra "Definido pelo servidor"; o POST manda só `rpe_target_source='protocol'` e o servidor grava o número; `rpe` segue o relatado: desde a §64.2, campo "RPE registrado nesta série" sempre visível, pré-preenchido com `targetRpe` e editável (apagado = `rpe` nulo; sem alvo = vazio) | 9/10 escrito na tela; alvo enviado em `rpe` sem passar pelo campo editável |
| Perfil do bloco e máquina de estados (`intensiveProfile`, `intensiveReducer`, `reconcileIntensiveBlock`, `intensiveItemDone`) | `frontend/src/lib/workout-mode-intensive.ts` (UI pura, sem número de regra) | `PlanExecutionScreen.tsx`, `IntensiveBlock`/`IntensiveBriefing`/`IntensiveSetEntry`/`QuickLoadAdjustment`/`IntensiveRest`/`IntensiveReview` | nº de séries, descanso ou alvo decididos na tela: séries = meta do item (`default_sets_count` do catálogo só sem meta), descanso = `rest_seconds` do item (sem ele, `DEFAULT_REST_SECONDS` de `prescription-defaults.ts`) |

**Contrato usado.** `POST .../sets` com `client_key` (UUID por série, o mesmo em toda nova tentativa), `reps_pending`
(sem reps válidas; nunca 0 nem a meta), `completed_at` (ISO com fuso), `planned_weight_kg` (= carga executada no
toque único), `load_adjust_source` (`fatigue`/`manual` só na série que estreia o ajuste) e `rpe_target_source`;
`PATCH .../sets/{n}` confirma reps pendentes e corrige reps/carga (404/409 viram mensagem); `GET /activities/{id}`
(com as pendentes) reconstrói o bloco sem snapshot. O fluxo normal passou a mandar `client_key` também
(série de trabalho, reps extras e preparatórias) e as preparatórias ganharam a trava de toque duplo (`submit-latch`).

**Recorte de entrega.** `INTENSIVE_MODE_TECHNIQUES = {fst7}` e `INTENSIVE_SUPPORTED_STAGE_KINDS = {working_block}`
dizem só quais técnicas já têm tela (PR 3: drop set, rest-pause, GVT, além da falha). Não são regra de carga,
esforço ou volume. Ampliados no PR 3 para as 8 técnicas do catálogo e as 3 famílias (ver §64).

**Impacto em produção.** Só frontend; nenhum SQL, migração ou Lambda. Itens sem técnica, ou com técnica fora do
recorte, seguem o fluxo de antes (os testes de `modo-treino/` continuam verdes). O `client_key` no fluxo normal usa
a idempotência já existente (087): a 1ª tentativa grava como antes; só a repetição muda (devolve a série gravada
em vez de duplicar). As séries do FST-7 passam a chegar com as colunas da 092, aceitas pela API desde o PR #617
(já na `develop`). Este PR NÃO pode ir para `main` sem o #617: a API antiga ignoraria `reps_pending` e gravaria a
série pendente como `reps` NULL "confirmada". Os dois sobem na mesma promoção em lote. No deploy, `frontend-deploy` depende de `backend-deploy`
(`deploy-producao.yml`), então a tela nova não fica no ar antes da API. Item com técnica fora do recorte (`gvt`,
drop set etc.) ou sem técnica segue o fluxo antigo: `resolveIntensiveProfile` devolve null (testes dedicados em
`workout-mode-intensive.test.ts`). Prova no artefato real: `next build` (export estático) no CI e, no smoke da
promoção, uma série normal e uma série FST-7 com `reps_pending` conferidas em `exercise_sets`.

**Rollback.** Reverter só o frontend é seguro (a API trata os campos novos como opcionais). Reverter a API (#617)
com este frontend no ar NÃO é: ver o rollback da §62.


## 64. Modo Intensivo para técnicas avançadas — PR 3, demais técnicas (2026-10-24)

Generaliza a tela da §63 para GVT, drop set, rest-pause e a família "além da falha" (`forced_reps`,
`negative_reps`, `partial_reps`, `cheat_reps`), e confirma as reps pendentes antes de finalizar o treino. Nenhuma
regra nova nem número novo no frontend: a semântica dos protocolos e a contabilização da Fase 5 (`technique_code` +
`anchor_set_number`, `set_type` do catálogo) não mudam.

| Regra | Dono | Consumidores | Proibido fora do dono |
|---|---|---|---|
| Carga de cada drop (redução prevista) | `api/app/domain/session_loads.py` (`technique_application_loads`, fator do drop) | `techniqueApplicationLoad` em `frontend/src/lib/workout-mode-load-suggestions.ts` (API `application_loads_kg` ou espelho com paridade) -> `intensiveApplicationPlannedLoad` em `workout-mode-intensive.ts`, gravada com `load_adjust_source='protocol'` | fator do drop escrito na tela ou recalculado na etapa |
| Redução extra por fadiga na etapa | `api/app/domain/session_loads.py` (`quick_load_adjust`) | `QuickLoadAdjustment` (espelho `mirrorQuickLoadAdjust`) -> `adjustApplicationLoad`, gravada com `load_adjust_source='fatigue'` (`'manual'` no "Editar") | percentual/passo escrito na tela |
| Pausa do rest-pause | prescrição congelada (`advanced_technique_parameters.pause_seconds`, de `progression_engine.technique_parameters`) e, sem ela, `advanced_technique_duration_seconds` do item | `resolveIntensiveProfile` (`techniqueDurationSeconds`) -> `IntensiveApplicationCard`/`IntensivePauseBanner` | segundos de pausa fixos na tela; usar o `rest_seconds` do item entre etapas |
| RPE-alvo das etapas e da família além da falha | `api/app/domain/training_safety.py` (`protocol_target_rpe`) | a tela lê `target_rpe` e o POST manda só `rpe_target_source='protocol'` (`intensiveApplicationPayload`, `intensiveSetPayload`); nas etapas e nas reps extras `rpe` vai nulo (sem RPE individual); na série do bloco, ver §64.2 | alvo enviado em `rpe`; RPE obrigatório na etapa |
| Séries com reps pendentes ao finalizar | `exercise_sets.reps_pending` (§62; motores ignoram até a confirmação) | `collectPendingRepsSets` (`frontend/src/lib/workout-mode-pending-reps.ts`) -> `PendingRepsFinishModal` (PATCH da série antes do `finish`; "Finalizar mesmo assim" mantém a pendência) | pendência deduzida de `reps` NULL sem a marca |
| Família do bloco, recorte e "reps extras" | `frontend/src/lib/workout-mode-intensive.ts` (UI pura: `intensiveStageKindOf` pelo catálogo, `isIntensiveBlockProfile`, `intensiveExtraRepsEnabled`, `intensiveExtraRepsPayload`) | `PlanExecutionScreen.tsx`, `IntensiveBlock`, `IntensiveSetEntry` | classificação de `set_type` à mão (usa `set-types.ts`) |

**Fluxo.** GVT = bloco do FST-7 (séries da meta do item, `default_sets_count` só sem meta; descanso = `rest_seconds`
do item). Além da falha = mesmo bloco sem briefing; na última série, "reps extras" com um toque viram UMA linha
do `set_type` da técnica no catálogo (hoje `forced_rep` nas quatro) ligada à série (a mesma regra do fluxo normal), com `client_key` próprio. Drop set/rest-pause NÃO usam a
máquina do bloco: a etapa, o total e a conclusão são os contadores da Fase 5 (`advancedStageOf`,
`buildAdvancedExerciseCounters`); a tela só troca o card. A última série de trabalho de um item com etapas abre a
1ª etapa sem o descanso do item (drop emenda; rest-pause começa pela pausa); entre etapas não há descanso do item.
Rest-pause repete a carga executada na etapa anterior (carga constante; um ajuste por fadiga continua valendo).

**Impacto em produção.** Só frontend; nenhum SQL, migração ou Lambda. As etapas passam a chegar com `rpe` nulo,
`rpe_target_source='protocol'`, `planned_weight_kg`, `load_adjust_source` e `completed_at` (colunas da 092,
aceitas desde o #617). A contabilização da Fase 5 com `rpe` nulo usa fator de esforço 1
(`technique_accounting.effort_factor`: "técnica é à falha"), o mesmo de antes quando o RPE da etapa chegava ≥ ao da
âncora. Itens sem técnica seguem o fluxo de sempre (testes de `modo-treino/` verdes). Prova no artefato real:
`next build` no CI e, no smoke da promoção, conferidos em `exercise_sets`: um drop set e um rest-pause
(`set_type`, `anchor_set_number`, `load_adjust_source`, `rpe` NULL, `rpe_target_effective`), uma série de GVT, uma
série além da falha com reps extras (linha ligada por `anchor_set_number`) e o modal de pendentes (PATCH confirma
`reps_pending = 0` e depois o `finish`).

**Rollback.** Reverter só o frontend é seguro: as linhas gravadas continuam válidas para a API e para os motores.

### 64.1 Achados do teste ponta a ponta (2026-10-24)

| Regra | Dono | Consumidores | Proibido fora do dono |
|---|---|---|---|
| Parâmetros da técnica SEM decisão do motor (ficha manual, `refresh-prescription`, decisão sem técnica ou com outra) | `api/app/domain/progression_engine.py` (`technique_parameters`, `source = TECHNIQUE_SOURCE_PLAN`), chamado por `prescription._technique_parameters` com aplicações/pausa da linha da ficha e o teto do par congelado | `target_rpe` = `training_safety.protocol_target_rpe` (o mesmo número que o POST grava por `effective_protocol_target_rpe`); briefing/cards leem `advanced_technique_parameters.target_rpe` | parâmetros montados na rota, no SQL ou na tela; "Definido pelo servidor" para item com técnica congelado depois desta mudança |
| Pausa padrão do rest-pause quando a ficha (ou o Modelo) não define `advanced_technique_duration_seconds` | `api/app/domain/progression_engine.py` (`REST_PAUSE_DEFAULT_PAUSE_SECONDS = 15`, decisão do usuário de 2026-10-25; `technique_pause_seconds(code, duração)`: a duração da linha sempre vence; só `rest_pause` ganha padrão, drop set e as demais técnicas sem duração ficam sem pausa) | `technique_parameters` grava `pause_seconds` pronto no snapshot (fontes `plan`, `model` e `progression`); Modo Treino normal e Intensivo leem pela mesma função (`techniquePauseSeconds` em `workout-mode-intensive.ts`: `pause_seconds` da prescrição desta técnica, senão a duração da ficha) para o timer e o `rest_seconds` da etapa; estimativa de tempo do Modelo (`periodization/time_estimate.py`) usa o mesmo padrão, e o espelho `template-time-estimate.ts` lê `time_estimate.rest_pause_default_pause_seconds` do `motor-constants.generated.json` (paridade em `template-time-estimate.parity.json`) | segundos de pausa escritos em tela, SQL, prompt ou RAG; padrão para drop set sem decisão do usuário (guarda `pausa_padrao_rest_pause`) |
| "Concluir" da etapa durante a pausa | UI pura (`intensivePauseHoldsCompletion` em `workout-mode-intensive.ts`; o tempo vem de `endsAt`) | `IntensiveApplicationCard`: bloqueado até o fim da pausa ou "Pular pausa" explícito | — |
| Linha das reps extras (além da falha) | contrato das etapas (`intensiveApplicationPayload`) | `intensiveExtraRepsPayload`: `planned_weight_kg` = carga executada na série, `rpe_target_source='protocol'`, `reps_pending=false`, `rpe` nulo; a revisão lista as extras (inclusive reconstruídas do servidor por `anchor_set_number`) | alvo em `rpe`; linha sem carga planejada |
| Rótulo das pendentes ao finalizar | UI pura (`pendingSetLabels` em `workout-mode-pending-reps.ts`: ordem entre as séries de trabalho + etapa pela âncora) | `PendingRepsFinishModal` ("série 2 · mini-bloco 2") | rótulo pelo `set_number` físico |

**Impacto em produção.** `_FREEZE_COLUMNS` passa a ler `advanced_technique_applications` e
`advanced_technique_duration_seconds` (colunas da 017, já existentes; SELECT só leitura, rótulo `ci-completo`).
Prescrições congeladas DAQUI em diante para linhas com técnica sem decisão do motor ganham
`advanced_technique_parameters` (antes null); snapshots já gravados não mudam até um novo congelamento. A carga das
etapas segue `session_loads` na leitura (o caminho novo não grava `application_loads_kg`). Linha de reps extras:
`rpe_target_effective` passa a ser o alvo do protocolo (antes o da prescrição) e ganha `planned_weight_kg`; a
contabilização da Fase 5 não muda (`rpe` continua nulo, fator de esforço 1). Linha com técnica e
`advanced_technique_applications` nulo usa o default do dono (`PROGRESSION_TECHNIQUE_APPLICATIONS`), como já fazia o
caminho da progressão.

Medido em produção (leitura, 2026-10-24): 45 linhas de `workout_plan_exercises` com técnica; 36 com
`advanced_technique_parameters` nulo hoje (rest_pause 20/22, drop_set 11/12, partial_reps 5/6; fst7 0/4, gvt 0/1);
nenhuma em ficha `planned` (0), então nada que o usuário vá abrir muda até o próximo congelamento. Sem pausa na
ficha: rest_pause 3/22 (desde 2026-10-25 recebem o padrão de 15 s do dono no próximo congelamento); sem aplicações: rest_pause 2/22 (default do dono).
`exercise_sets` tem 11 linhas `forced_rep` históricas, intocadas (a 092 ainda não está em produção). Prova no artefato real: smoke da
promoção conferindo `prescription_json.advanced_technique_parameters.target_rpe` de uma linha manual com técnica e,
em `exercise_sets`, a linha `forced_rep` com `planned_weight_kg` e `rpe_target_source='protocol'`.

**Rollback.** Reverter é seguro: o campo é aditivo no snapshot e as linhas gravadas continuam válidas.

### 64.2 RPE registrado pré-preenchido na série do bloco (2026-10-29)

Decisão do usuário de 2026-10-29. Substitui, só na série do bloco (`working_block`: GVT, FST-7, além da falha), a
de 2026-10-24 ("nunca gravar o alvo em `rpe`"). Em produção, o usuário viu "RPE-alvo: RPE 10" e esperava que esse
valor fosse registrado, mas o RPE só ia no POST quem tocasse em "Informar RPE".

| Regra | Dono | Consumidores | Proibido fora do dono |
|---|---|---|---|
| Valor inicial do RPE relatado na série do bloco | alvo pronto do backend (`training_safety.protocol_target_rpe`, via `advanced_technique_parameters.target_rpe` -> `resolveIntensiveProfile().targetRpe`) | `IntensiveSetEntry`: `intensiveRpeInitialText(targetRpe)` preenche o campo "RPE registrado nesta série", sempre visível e editável; `parseRpeInput` converte o texto para o `rpe` do POST (`intensiveSetPayload`): apagado ou inválido vira nulo. Sem alvo (snapshot antigo), o campo começa vazio. Série nova: o campo volta ao alvo, porque `IntensiveBlock` remonta a entrada com `key={state.currentSet}` | número de RPE escrito na tela; RPE preenchido sem o campo editável; pré-preenchimento nas etapas de drop set e rest-pause (`intensiveApplicationPayload`) ou nas reps extras (`intensiveExtraRepsPayload`), que continuam com `rpe` nulo |

**Confirmação e correção.** `UPDATE_RECORD` sem `rpe` preserva o RPE enviado. O `PATCH .../sets/{n}` (confirmação
de reps pendentes) não manda `rpe`, e o servidor mantém o gravado (`merge_set_patch`: o que vem vale, o que falta
fica). O backend não muda: `add_session_set` já aceitava `rpe` relatado junto de `rpe_target_source='protocol'`.

**Impacto em produção.** Só frontend, sem SQL, migração ou Lambda. Medido em 2026-10-29 (`exercise_sets` com
`rpe_target_source='protocol'`): 16 linhas, todas com alvo 10. São 10 `working` com `rpe` NULL (exercício 812, em
2026-10-29), 3 `working` com RPE relatado (2026-10-26, pelo "Informar RPE"), 2 `rest_pause` NULL e 1 `forced_rep`.
Daqui em diante, a série do bloco chega aos motores com o RPE do campo (normalmente o alvo). As linhas antigas
continuam NULL: o usuário decidiu não corrigi-las. Efeitos, todos pelas regras dos donos, sem regra nova:
`progression_engine.evaluate_track_session` deixa de ficar NEUTRA por `REASON_RPE_MISSING`, porque a última série de
trabalho (`effort_sets`) passa a ter RPE. Assim, a sessão qualifica ou zera pelas regras de carga, reps e esforço. A mudança que mais se sente: uma sessão intensiva que antes ficava NEUTRA agora pode dar RESET e zerar a sequência da trilha (`REASON_LOAD_BELOW_TRACK`, `REASON_REPS_BELOW_MIN`, `REASON_REPS_BELOW_MAX`). Na hipertrofia, RPE 10 nunca fica abaixo do alvo, então não há `REASON_RPE_BELOW_TARGET`. Na força, RPE acima do alvo dá `REASON_RPE_ABOVE_TARGET`. Hoje isso alcança 2 exercícios com série `working` de protocolo, o de 2026-10-26 e o 812. Só as sessões novas entram.
`next_set_load` passa a ver o RPE na leitura da série (`RPE 10 antes de 6 reps` reduz). `safety_event` só aparece se
o RPE registrado passar do teto do par. O alvo 10 só existe com falha liberada (`protocol_target_rpe`); a exceção é
o teto ter caído depois do congelamento, quando a tela mostra o congelado e o servidor grava o menor
(`effective_protocol_target_rpe`). No e1RM, RPE 10 é RIR 0 (`e1rm.rir_from_rpe`): os recordes de `analytics`
(`RIR_ZERO`) não mudam, e o `exposure_service` e o `dashboard/comparability` (`SKIP`) passam a ver essas séries. A
dose de recuperação (`e1rm.dose_inputs`) usa o RPE informado no lugar de `DOSE_RPE_WHEN_MISSING`.

**Rollback.** Reverter só o frontend volta ao link opcional. As linhas gravadas continuam válidas.

## 65. Veredito único no card da série do Modo Treino — M96 (2026-10-25)

Achado do E2E do Modo Intensivo no DEV (24/10): depois de 12 reps @ RPE 9 o card mostrava ao mesmo tempo
"Esforço abaixo do alvo" (status de esforço, alvo 10) e "Repetições e RPE dentro do esperado" (motivo do MANTER da
carga da próxima série, que não olhava o alvo). Reproduz na `develop` de 25/10 sempre que o alvo cobrado passa do
RPE executado e nenhuma regra de carga casa. As duas mensagens passam a derivar do MESMO status.

| Regra | Dono | Consumidores | Proibido fora do dono |
|---|---|---|---|
| Alvo de esforço COBRADO numa série: o efetivo, restringido pelo degrau da rampa planejada (`"8-9-10"`: 8/9/10 por série; só restringe) | `api/app/domain/progression_engine.py` (`set_effort_target`; degrau por `next_set_load.planned_ramp_rpe`, o mesmo da carga da próxima série) | `effective_intensity.set_intensity_reading(planned_rpe=...)` (salvamento da série, repetição por `client_key`, confirmação das reps pendentes, IA por série); `SetIntensityReading.target` é o alvo cobrado | comparar o RPE da série com o topo da rampa fora do dono; recalcular o degrau na tela |
| Alvo cobrado numa série do Modo Intensivo (`rpe_target_source='protocol'`) | `api/app/domain/effective_intensity.py` (`with_protocol_target`: o alvo do protocolo gravado em `rpe_target_effective` substitui o da prescrição no status; teto/falha não mudam; a rampa não restringe) | `workout_sessions._evaluate_set(protocol_target=...)`: POST resolve o alvo do protocolo ANTES da leitura; repetição e PATCH de confirmação leem o gravado (`_stored_protocol_target`) | status do Modo Intensivo cobrado contra o alvo da prescrição |
| Motivo do MANTER da carga da próxima série quando nenhuma regra casa: pelo status de esforço da MESMA série (`BELOW_TARGET` -> `NEXT_SET_HOLD_EFFORT_BELOW_TARGET`; `ABOVE_SAFE_CEILING` -> `NEXT_SET_HOLD_ABOVE_SAFE_CEILING`; atingido/ausente -> `NEXT_SET_WITHIN_RULE`/`NEXT_SET_WITHIN_PLANNED_RAMP` como antes). A direção e a carga não mudam; regra que casa (aumentar/reduzir) vence | `api/app/domain/next_set_load.py` (`SetFacts.intensity_status`, `_HOLD_REASON_BY_INTENSITY_STATUS`; rótulos com o texto do status interpolado de `intensity_status_labels`) | `engine_decision_context.next_set_load_for(intensity_status=reading.status)` no salvamento e na IA por série; o Modo Treino lê `reason_label` (fallback: `motor-constants.generated.json`, bloco `next_set_load.reason_labels`) | "dentro do esperado" ao lado de esforço abaixo do alvo; texto do motivo escolhido na tela (guarda `motivo_manter_carga`) |

**Impacto em produção.** Nada é gravado de novo: `intensity_status` e `next_set_load` só existem na resposta do
salvamento e no prompt da IA por série; `rpe_target_effective` gravado não muda (o status da SESSÃO, §5.7, continua
cobrando o alvo cheio na última série). Sem SQL nem migração. Medido em produção (leitura, 2026-10-25, últimos 60
dias): 1035 séries de trabalho, 619 com alvo e RPE; 538 sairiam MANTER e 20 delas (3,7%) com esforço abaixo do
alvo, exatamente as que mostravam a contradição e agora mostram "Esforço abaixo do alvo: mantenha a carga e busque
o RPE-alvo". Nenhuma série com rampa de RPE (degrau) nem com `rpe_target_source='protocol'` no período: as duas
mudanças de alvo cobrado não alteram nenhum status já visto em produção. Prova no artefato real: smoke da promoção
salvando 12 reps @ RPE 9 num item com alvo 10 e conferindo `intensity_status=BELOW_TARGET` com
`next_set_load.reason=NEXT_SET_HOLD_EFFORT_BELOW_TARGET`.

**Rollback.** Reverter é seguro: os dois códigos de motivo são aditivos (o frontend cai no `reason_label` do
servidor) e nada foi persistido.

## 66. Objetivo de treino: hipertrofia ou força — M97, F0 fundação de dados (2026-10-25)

Pedido do usuário: o app (até aqui só hipertrofia) passa a suportar o objetivo FORÇA. Decisões: eixo binário
`hypertrophy | strength`; o objetivo é do Modelo, com override por item do slot; e1RM/recorde continuam únicos;
progressão separada por objetivo (F1). A F0 só cria e propaga o dado: **nenhum motor lê o objetivo ainda**.

| Regra | Dono | Consumidores | Proibido fora do dono |
|---|---|---|---|
| Vocabulário (`TrainingGoal`: `HYPERTROPHY`, `STRENGTH`; `GOAL_VALUES`), default (`DEFAULT_GOAL` = hipertrofia), leitura estrita da API (`parse_goal`/`optional_goal_value` -> 422) e tolerante do banco (`read_goal`/`goal_value`: ausente/desconhecido = default; `read_override`/`override_value`: NULL = herda) | `api/app/domain/training_goal.py` | `models.py` (cabeçalho do Modelo, item do slot, linha da ficha), `template_versions` (versão e item), `workout_plan_templates` (criação, edição, duplicação, reversão, leitura), `services` (linha da ficha), `domain/prescription` (snapshot) | literal `'hypertrophy'`/`'strength'` em `api/app` (guarda `objetivo_treino_literal`); default decidido na rota |
| Precedência: override do item do slot > objetivo do Modelo > default (`resolve_goal`, `resolved_goal_value`); Modo A (itens por grupo): o do primeiro item do grupo no slot (`goal_by_group`) | `api/app/domain/training_goal.py` | `generation/mode_b.py` (item a item), `generation/mode_a.py` (por grupo) | resolver o objetivo na tela, no SQL ou no motor |
| Predicado SQL (`sql_goal_is(alias, param)` = `<alias>.training_goal = <param>`, valor sempre parâmetro) e carimbo do registro (`SQL_PLAN_EXERCISE_GOAL` + `plan_exercise_goal_params`: o da linha da ficha de origem; sem ficha = default) | `api/app/domain/training_goal.py` | `workout_sessions.add_session_exercise` (INSERT de `exercises`); F1 passa a filtrar as leituras por `sql_goal_is` | filtro de objetivo escrito à mão |

**Onde o dado mora (migração 093).** `training_goal ENUM('hypertrophy','strength') NOT NULL DEFAULT 'hypertrophy'`
em `workout_plan_templates`, `workout_plan_template_versions`, `workout_plan_exercises`, `exercises`,
`progression_decisions`, `progression_tracks` e `progression_group_states`; `NULL` (= herda) em
`workout_plan_template_slot_items`. O objetivo entra nas chaves únicas `uk_ptr_user_pair_context` e
`uk_pgs_user_group` (recriadas com o mesmo nome, num ALTER só). A migração é autocontida; o teste
`test_migration_093_plano.py` prova que o ENUM dela é o `GOAL_VALUES` do dono.

**Propagação gravada.** Modelo (cabeçalho e item) -> versão (o cabeçalho e o item entram na forma canônica SÓ quando
o objetivo difere do default/NULL: Modelo sem objetivo tem a mesma forma de antes e nenhuma versão espúria nasce) ->
rascunho da ficha (Modos A/B, `training_goal` resolvido) -> `workout_plan_exercises.training_goal` (ecoado pelo
cliente; ausente = default; a edição preserva o gravado por `workout_plan_exercise_merge`) -> `prescription_json`
(`training_goal`, aditivo; snapshot antigo = hipertrofia) -> `exercises.training_goal` (carimbo no registro ao vivo;
importação do relógio e treino livre = default). Trilhas, estados de grupo e decisões da progressão gravam o default
pela coluna (F1 passa a separar).

**Guarda.** Padrão novo `objetivo_treino_literal` (só Python de `api/app`). Allowlist com duas entradas de OUTRA
semântica, permanentes: `intervals_activity_kind.py` (2: tipo de atividade do Intervals.icu) e `body_map_render.py`
(1: atributo `strength` do destaque do mapa corporal).

**Impacto em produção.** Nenhuma regra muda e nenhum motor lê a coluna: toda linha existente vira hipertrofia pelo
DEFAULT, que é o que o app sempre prescreveu. A forma canônica das versões de Modelo existentes é idêntica (teste
`test_training_goal_versoes.py`), então nenhum PUT abre versão nova por causa do campo. As chaves únicas novas são
um superconjunto das antigas: nenhuma linha existente pode violá-las, e o `UPDATE ... WHERE (chave antiga)` + INSERT
de `progression_state.save_track`/`save_group_state` continua achando a mesma linha (só existe hipertrofia).
Medido em produção (somente leitura, Data API, 2026-10-25): Aurora 3.08.2 / MySQL 8.0.39; as 8 tabelas em
`ROW_FORMAT=Dynamic` com `TOTAL_ROW_VERSIONS` entre 0 e 7 (limite do INSTANT: 64); 14 Modelos, 32 versões, 1355 itens
de slot, 549 linhas de ficha, 668 `exercises`, 309 decisões, 203 trilhas e 19 estados de grupo, todos sem a coluna
(viram hipertrofia); chaves atuais `uk_ptr_user_pair_context (user_id, exercise_id, equipment_id, fatigue_context)` e
`uk_pgs_user_group (user_id, muscle_group_id)`; última migração aplicada 092. A FK `fk_pgs_user` só tem
`uk_pgs_user_group` como índice iniciado por `user_id`: por isso a chave é recriada num ALTER só (o MySQL confere o
estado final, e a chave nova também começa por `user_id`); em `progression_tracks` há ainda
`idx_ptr_user_group_executed`.

**Dívidas registradas para a F1.**
- `progression_state` (`load_group_states`, `save_group_state`, `load_tracks`, `save_track`),
  `engine_decision_context` e `exercise_briefing` leem/gravam trilhas e estados SEM o objetivo no WHERE/JOIN. Inócuo
  enquanto só existe hipertrofia; a F1 inclui `training_goal` em todos eles antes de qualquer gravação de força.
- Os geradores de fallback (`generation/mode_a_legacy.py`, `generation/mode_b_legacy.py` e o v1 de
  `workout_plan_templates_engine.py`, fallback esperado zero) não propagam o objetivo: a linha cai no default. Ficam
  fora até a remoção do legado; a F1 propaga ou bloqueia o fallback para Modelo de força.
- O cliente (`frontend/src/lib/workout-plan-payload.ts`) ainda não ecoa `training_goal` do rascunho: até a F3 a linha
  da ficha grava o default mesmo num Modelo de força (nenhum Modelo de força existe; a tela ainda não oferece o campo).
- O `down` da 093 só é rollback seguro enquanto nada grava `strength` (solta as colunas). Depois da F1, reverter é por
  migração nova.

**Ordem do deploy (conferida em `.github/workflows/deploy-producao.yml`, job `backend-deploy`).** O passo (b)
`migrate` (Lambda `musclefit-migrations`) roda ANTES do passo (c), que atualiza as 7 Lambdas da aplicação, e o gate
`RUN_DB_MIGRATIONS` está `true` no repositório. Se (b) falhar, o job para e as Lambdas ficam com o código anterior,
que funciona com o schema novo porque a 093 é só aditiva. Por isso o código lê `training_goal` sem tolerar a coluna
ausente, como a 092 fez com `reps_pending`. Testes que desfazem e refazem migrações anteriores (a 065 em
`test_template_versions_065.py`, a 036/068 em `test_pr10_progressao_shadow.py`) incluem a 093 entre as posteriores.

### 66.1 F1 — isolamento da progressão e das leituras por objetivo (2026-10-25)

Decisão do usuário: progressão, platô, comparabilidade, sugestão de carga e fases SEPARADOS por objetivo; e1RM e
recorde ÚNICOS (chave física). A F1 não cria perfil de força (F2): com um objetivo só (todo o histórico), tudo é
idêntico ao de antes.

| Regra | Dono | Consumidores | Proibido fora do dono |
|---|---|---|---|
| Identidade da trilha `(exercício, equipamento, contexto de fadiga, objetivo)` (`track_key`, `TrackState.key`); rótulo da evidência e alvo do `pending_payload.track_key` no formato de sempre no default (`track_label`, `track_key_payload`: 3 posições; 4 só fora do default); `resolve_track(..., training_goal)` só vê trilhas do objetivo; `GroupState.key = (grupo, objetivo)` e `new_group_state(..., objetivo)` | `api/app/domain/progression_engine.py` | `progression_evaluator`, `progression_state`, `generation/deterministic_prescription` (`PairRequest.training_goal`), `generation/mode_a`/`mode_b`/`freeze_mode_a`/`freeze_mode_b`, `domain/prescription_service` (`PairQuery.training_goal`) | `tracks.get(par)`/trilha sem objetivo; payload com 4 posições no default |
| Avaliação por objetivo: cada objetivo da sessão é avaliado pelo núcleo (`evaluate_session`) só com as execuções dele (`SessionRecord.only_goal`) e os estados dele; janela = as mesmas últimas sessões (finish e replay iguais); sessão sem execução do objetivo não é avaliada nele; teto de ativação e contexto de fadiga leem a sessão INTEIRA (`evaluate_session_by_goal`, `evaluate_history_by_goal`, `goal_group_states`, `goal_tracks`) | `api/app/domain/progression_evaluator.py` | `progression_state.run_session_evaluation`/`rebuild_for_user` | partição por objetivo fora do avaliador |
| Objetivo de cada execução no histórico (`ExerciseExecution.training_goal`, de `exercises.training_goal` em `SESSIONS_SQL`; `SessionRecord.training_goals`/`only_goal`) | `api/app/domain/training_history.py` | avaliador, `exercise_briefing` | — |
| Persistência com o objetivo no WHERE/INSERT: `load_group_states(objetivo)`/`load_all_group_states`, `load_tracks` (todos, chave com objetivo), `save_track`/`save_group_state` (`sql_goal_is` da tabela), `insert_decision(training_goal=...)`, `persist_goal_evaluation`; `state_snapshot` igual ao de antes no default (outros objetivos em `groups_by_goal`) | `api/app/progression_state.py` | `replay_progression` (fingerprint) | — |
| Comparabilidade: `ComparabilityKey.training_goal` (fora de `to_dict`, contrato da API igual); exposição de OUTRO objetivo é PULADA, sem entrar na sequência nem a interromper, como o contexto de fadiga (`trailing_comparable_indices`, `goals_match`; decisão do Tech Lead no PR #627), e marcada `MISMATCH_TRAINING_GOAL` contra a chave atual; `physical` sem objetivo nem contexto (recorde/e1RM únicos) | `api/app/intelligence/dashboard/comparability.py` | `exposure_service`, `exercise_performance` (sinal "Desempenho recente": troca de objetivo = sem par) | comparar exposições de objetivos diferentes |
| Filtro SQL: `sql_goal_is` (valor) e `sql_goal_join` (mesmo objetivo nos dois lados de um JOIN) | `api/app/domain/training_goal.py` | `exposure_service` (`EXPOSURES_SQL_TEMPLATE`, `BATCH_EXPOSURES_SQL_TEMPLATE`, `PROGRESSION_DECISIONS_SQL`), `services.get_last_session_sets`, `exercise_briefing` (`_track`), `progression` (`/progression/tracks`), `analytics._group_phases`, `progression_state` | `<alias>.training_goal = ...` escrito à mão (guarda `objetivo_treino_filtro_sql`, allowlist vazia) |
| Carimbo do objetivo na ficha PELO SERVIDOR no congelamento (override do item > Modelo > default; Modo B pelo casamento `match_rows_to_model_items`, Modo A pelo grupo PRIMARY; ficha sem Modelo intocada; UPDATE só da linha que muda) | `api/app/generation/plan_goal_stamp.py` (precedência no dono `training_goal`) | `services._freeze_plan_prescriptions` (antes do snapshot e da progressão) | depender do cliente ecoar `training_goal` |
| Parâmetro `goal` das rotas de leitura (ausente = default; inválido = 422) | `api/app/training_goal_query.py` | `/analytics/dashboard/exercises/{id}/progress`, `/progression/tracks`, `/analytics/dashboard-progression-by-muscle-group` (fase), `/exercises/last-session-sets` (sem `goal`: o da linha da ficha da sessão atual, `services.session_item_goal`) | default decidido na rota |

**Limite conhecido da guarda.** `objetivo_treino_filtro_sql` só casa predicado com alias (`<alias>.training_goal = ...`): `training_goal = %s` sem alias é também a forma dos `SET` legítimos de gravação (F0), então não entra na regex; filtro sem alias fica para a revisão (`guardiao-motores`).

**O que NÃO separa objetivo nesta fase.** Recuperação/fadiga (dose, contexto de fadiga prévia, "Fadiga atual do
grupo") conta tudo. Volume, tonelagem por grupo e o comparativo T-0..T-3 por slot (`slot_session_comparison`: a
identidade é o slot do Modelo, e volume só pondera o objetivo na F2) não filtram. e1RM e recorde
(`session_records`, `domain/e1rm.py`, recorde de `last-session-sets`) seguem únicos. `next_set_load`/
`engine_decision_context.next_set_load_for` e `session_loads` são regras da sessão em andamento (sem histórico).
Pendência da F4: o bloco `records` do drill-down (`/analytics/dashboard/exercises/{id}/progress`) hoje sai das exposições do objetivo filtrado; na F4 ele volta à chave física (todos os objetivos), como o recorde canônico. A tabela de `/analytics/dashboard-progression` lista as sessões do exercício sem filtro (a seta "Evolução" vem das
exposições do objetivo default): filtro de gráfico é da F4. **Resolvido na F4 (§66.4):** `records` volta à chave física
(todos os objetivos, sem deload) e a tabela ganha `?goal=` opcional.

**Dívidas da F0 resolvidas aqui.** Trilhas/estados/decisões com o objetivo no WHERE/INSERT (`progression_state`,
`exercise_briefing`); o carimbo não depende mais do cliente (`plan_goal_stamp`); os geradores legados
(`mode_a_legacy`/`mode_b_legacy`, fallback - produção roda `GENERATION_ENGINE=deterministic`) ficam cobertos porque o
carimbo sai do Modelo da ficha, não do rascunho. Seguem para a F2/F3: perfis de força nos motores, o campo na tela, e
o v1 de `workout_plan_templates_engine.py` (fallback esperado zero).

**Impacto em produção (medido).** Toda linha de produção é hipertrofia (F0, medido 2026-10-25). Paridade do replay
antes × depois: `tests/unit/test_m97_f1_paridade_replay.py` reprocessa um histórico de 30 sessões (progresso,
mudança de faixa, aumento/redução de carga, regressão D10 com `pending_payload.track_key` no estado final) pelo
caminho de `rebuild_for_user`; `fingerprint` do `state_snapshot` e md5 das decisões calculados sobre o código de
`origin/develop` ANTES da F1 (commit `fdf23ae`) e iguais depois (`c903dbc0…`, `2a5bdda6…`). Em produção, a prova é o
`replay-progression` (`db-migrations.yml`, com aval) depois da promoção: o `fingerprint` por usuário tem de ser o
mesmo de uma rodada imediatamente anterior ao deploy (usar `replay-dry-run`, que não grava). **Pré-requisito:** a 093 aplicada no Aurora ANTES do código (promoção com `RUN_DB_MIGRATIONS=true` ou `db-migrations.yml action=migrate` prévio); sem ela as leituras de `training_goal` dão 1054.

### 66.2 F2 — perfil de FORÇA nos motores donos (2026-10-25)

Pedido do usuário (épico M97): força passa a ter prescrição própria. Regra da F2: **cada número de força nasce como
constante nomeada no motor dono, indexado pelo objetivo** (`training_goal`); hipertrofia (default) sai byte a byte
igual. Nenhuma tela, SQL ou prompt define número de força (frontend é F3/F4; RAG/prompts/periodização por IA é F5).

| Regra | Dono | Consumidores | Proibido fora do dono |
|---|---|---|---|
| Defaults de prescrição por objetivo: `defaults_for(goal)` → `PrescriptionDefaults` (força: reps `"3-5"` e RPE `"7-9"` DERIVADOS de `progression_engine.STRENGTH_REPS` e de `STRENGTH_RPE_FLOOR`/`training_safety.STRENGTH_RPE_CEILING`, descanso 180–300 s, ponto médio 240 s, 3 séries, teto de sanidade 300 s; `sets_count`, `rpe_target` e o teto de sanidade ainda sem consumidor: reservados para a F3, `/training-goals` e os campos do editor); `item_rest_seconds(goal, min, max)` (hipertrofia: o ponto médio do intervalo PEDIDO na geração, como sempre; força: o do perfil dela); `item_reps_target(goal)`; `rest_seconds_sanity_max(goal)` | `domain/prescription_defaults.py` (`STRENGTH_*`; `HYPERTROPHY_DEFAULTS` = as constantes de sempre) | `generation/mode_b.py` (descanso e reps default do item), `generation/mode_a.py` (por grupo) | descanso/reps de força escritos na geração ou na tela |
| Escada de progressão por objetivo: `GoalLadder`/`ladder_for(goal)`. Força = UMA fase de carga (`STRENGTH_LOAD_PHASE`, faixa `STRENGTH_REPS` 3–5; sem F1→F2, sem F3/F4 técnica, sem F5 série); qualifica no topo com RESERVA (RPE ≤ alvo; senão `REASON_RPE_ABOVE_TARGET`), sem regra de excesso; o platô que sobe a carga usa `STRENGTH_PLATEAU_MIN_SESSIONS`/`STRENGTH_PLATEAU_MIN_SPAN_DAYS` e os incrementos de sempre (composto 5 kg, isolador 2,5 kg); o platô de força por ESTAGNAÇÃO (`STRENGTH_STALL_SESSIONS` sessões sem qualificar na carga atual) recua a carga DA TRILHA para `STRENGTH_PLATEAU_RESET_FACTOR` × carga, no passo do equipamento e ao menos um passo abaixo (`strength_stall_triggered`, `strength_plateau_reset_load`, `apply_strength_plateau_reset`; evidência `strength_plateau_reset`), no lugar da D10. É o RECUO DE PLATÔ da trilha, não o deload (período/sessão de deload, dono futuro `domain/deload.py`); Modo A com teto `STRENGTH_MAX_SETS_COUNT`; `engine_version` = `STRENGTH_ENGINE_VERSION` (`progression-v2.0.0+strength-v1`) e evidência `ladder` só no ramo de força; `new_group_state`/`empty_group_state`/`reps_for_phase` por objetivo; `plateau_confirmed(min_sessions, min_span_days)` (default = hipertrofia) | `domain/progression_engine.py` | `domain/progression_evaluator.py` (escada do objetivo avaliado, recuo, versão), `generation/deterministic_prescription.py` (`empty_group_state`) | fase, faixa ou recuo de força decididos fora do motor |
| Intensidade e técnicas por objetivo: `compose(..., training_goal=)` (força: alvo `STRENGTH_TARGET_RPE` 8, teto `STRENGTH_RPE_CEILING` 9, falha nunca prescrita; clamp segurado pelo teto do objetivo = `REASON_TRAINING_GOAL`; hipertrofia = a composição de sempre); `safety_for_goal(safety, goal)` tira `STRENGTH_DISALLOWED_TECHNIQUES` (`drop_set`, `rest_pause`, `fst7`, `gvt`; bi-set não existe no catálogo; a família "além da falha" cai pelo teto 9) com `TECHNIQUE_NOT_ALLOWED_FOR_GOAL`; `goal_target_rpe`, `goal_rpe_ceiling`, `goal_allows_technique` | `domain/training_safety.py` | `progression_engine.prescribe`, `progression_evaluator.SessionInputs.composition_for` (objetivo = 4ª posição da chave da trilha), `domain/prescription.build_prescription`/`apply_safety_overlay` (objetivo da linha/snapshot) | teto/alvo de força ou lista de técnicas proibidas fora do dono |
| Carga da próxima série de força: `STRENGTH_RULESET` (em código, `RULESET_SOURCE_STRENGTH` = `strength-v1`, `STRENGTH_RULESET_VERSION`; piso/teto = `STRENGTH_REPS`; 1. reps > teto + `EXCESS_REPS_FAST_TRACK` → +`STRENGTH_NEXT_SET_LARGE_INCREASE_PERCENT`%; 2. reps ≥ teto com RPE < `STRENGTH_NEXT_SET_LOW_RPE_BELOW` → +`STRENGTH_NEXT_SET_INCREASE_PERCENT`%, respeita a rampa; 3. RPE 10 antes do piso → −`STRENGTH_NEXT_SET_DECREASE_PERCENT`%); `ruleset_for_goal(goal, published)` (hipertrofia: o PUBLICADO, como sempre; força: o de força) e `ruleset_uses_published(goal)` (força não lê o publicado) | `domain/next_set_load.py` | `workout_sessions._evaluate_set` (objetivo do snapshot da linha, `effective_intensity.plan_training_goal`), `workout_ai_generation_core` (IA por série: `PairEngineContext.training_goal` do exercício em andamento) | percentual/limiar de força fora do dono; escolher o conjunto pelo objetivo fora de `ruleset_for_goal` |
| Limites e tempo de sessão por objetivo: `STRENGTH_SESSION_EXERCISES_MIN` e `session_exercises_min(goal)`; `slot_items_goal(items, template_goal)` (força só se TODOS os itens de equipamento resolvem para força); `complement_default_rest_seconds(item, template_goal)` (força: teto do intervalo dela, 300 s; hipertrofia: os 90 s de sempre); `estimate_slot_time(..., default_rest_for_item=)` | `periodization/limits.py`, `periodization/time_estimate.py` | `periodization/validation.py` (`SLOT_TOO_FEW_EXERCISES` pelo piso do objetivo do slot), `periodization/volume_complement.py` (conta de tempo do complemento por item) | piso/descanso de força escritos no validador ou no complemento |
| Peso da série de força no volume HIPERTRÓFICO: `STRENGTH_SET_HYPERTROPHY_VOLUME_WEIGHT`, `hypertrophy_volume_weight(goal)`, `hypertrophy_volume_units(units, goal)` (hipertrofia: as mesmas `units`, sem multiplicar); `MethodologyItemInput.training_goal` (o motor escala o peso do bloco do item) | `intelligence/methodology_volume.py` | `VolumeService.get_muscle_group_overview` (séries diretas/indiretas/estabilizadoras e de estímulo: status semanal, metas, mapa corporal; `sets_count`, fadiga e dose seguem cheias), `VolumeService.get_daily_muscle_activation` (metodologia do realizado, janela de ativação do Modo A), `intelligence/progression_activation.realized_items` (teto de ativação) | multiplicar o peso fora do dono; ponderar dose, fadiga ou recuperação |

**Guarda.** Padrão novo `objetivo_treino_ramo`: `TrainingGoal.STRENGTH`/`HYPERTROPHY` só nos 7 donos acima, com
allowlist vazia. Os demais números de força são constantes nomeadas nos donos e entram pelos padrões existentes.

**O que NÃO muda.** Recuperação e fadiga contam 100% (dose, `fatigue_equivalent_sets`, contexto de fadiga prévia,
`MuscleRecoveryService`). e1RM e recorde continuam únicos. O conjunto PUBLICADO da próxima série (088,
`/regras-carga`) continua sendo o de hipertrofia. O espelho do frontend (`motor-constants.generated.json`) não muda:
as seções exportadas são as de hipertrofia, e o perfil de força chega às telas na F3 (`/training-goals`).

**Leitura do objetivo na sessão em andamento.** `effective_intensity.plan_training_goal(prescription_json)` (o
`training_goal` do snapshot congelado, o mesmo carimbado em `exercises`; sem snapshot = default) e
`engine_decision_context.PairEngineContext.training_goal` (o do exercício em andamento do par). Ninguém lê o objetivo
do item por outro caminho para escolher regra.

**Limites conhecidos (para F3/F4/F5).**
- Pontos que ainda compõem/validam SEM o objetivo (a técnica e o teto de força valem depois, na prescrição do motor,
  que remove a técnica e corta o RPE): `template_safety_validation` (aviso de RPE/técnica do Modelo usa o teto do par),
  `generation/exercise_substitution.py`, `generation/mode_b.py` (validação da técnica do Modelo na escolha do par),
  `workout_plan_equipment.py` e `domain/equipment_ranking.py` (ranking/escolha de equipamento pela técnica do Modelo).
  Uma substituição pode preferir um par compatível com uma técnica que a força vai remover. Ficam para a F3/F5.
- `effective_intensity.from_legacy_plan`/`from_safety` (ficha legada e treino livre) seguem sem objetivo: os dois são
  hipertrofia por definição. O legado (`mode_a_legacy`, `mode_b_legacy`, `abc_volume_reinforcement`,
  `workout_plan_templates_engine` v1) usa os defaults de hipertrofia: fallback esperado zero, só hipertrofia.
- No ramo de força, com par que libera a falha, a negação sai `failure_denied_by = RECOVERY` (evento informativo, não
  de segurança): o contrato não tem valor "pelo objetivo". O rótulo próprio é da F3/F5.
- O validador de segurança do Modelo (`template_safety_validation`) ainda não avisa técnica metabólica em item de
  força. A geração remove a técnica e avisa `MODEL_TECHNIQUE_INVALID` na ficha. O auto-fix no validador da IA é da F5.
- O complemento de volume (`volume_complement`) lê só o override do item. O objetivo do Modelo entra na F5, com
  `PeriodizationDraftIn.training_goal`.
- Leituras de volume que AINDA contam a série de força cheia (gráficos e painéis, sem efeito em prescrição): tendência
  semanal (`get_rolling_weekly_trend_by_muscle_group`), ranking de exercícios, linha do tempo de sessões, razão
  estímulo/fadiga, séries equivalentes e ativação por atividade, `dashboard/muscle_summary_service`,
  `adherence_service` e `report/daily_breakdown`. A ponderação delas é da F4, junto do filtro por objetivo.
  **Resolvido na F4 (§66.4).**

**Impacto em produção.** Toda linha de produção é hipertrofia (medido em 2026-10-25 na F0/F1; a tela ainda não
oferece força). Com só hipertrofia, cada ramo novo devolve o valor de antes:
- `defaults_for(None)` são as constantes de sempre;
- `compose(..., training_goal=None)` = `compose(...)`, com teste exaustivo teto × prontidão × prescrito;
- `safety_for_goal` devolve o mesmo objeto;
- `ladder_for(None)` usa as constantes de sempre, sem evidência nova e com o mesmo `engine_version`;
- `ruleset_for_goal(None, publicado)` é o publicado;
- `hypertrophy_volume_units` devolve as mesmas `units`.

Paridade do replay (`tests/unit/test_m97_f1_paridade_replay.py`): `fingerprint` `c903dbc0…` e decisões `2a5bdda6…`
IDÊNTICOS aos da F1. SQL: só a coluna `e.training_goal` entra no SELECT da janela do overview
(`VolumeService._WINDOW_EXERCISE_SELECT`, `recovery_read_model.WINDOW_SETS_SQL`) e no SELECT/GROUP BY de
`get_daily_muscle_activation`. Como `e.id` já está no GROUP BY, o agrupamento não muda. A coluna existe desde a 093.
Testes de igualdade com linhas reais do `VolumeService` (overview e ativação diária): linha de hipertrofia = linha sem a
coluna, campo a campo; força pondera só os campos de estímulo/diretos (`sets_count`, fadiga e dose seguem cheios).
**Pré-requisito (o mesmo da F1):** a 093 aplicada no Aurora ANTES do código; a F2 amplia as leituras de
`exercises.training_goal` para caminhos quentes (overview de volume/recuperação, ativação), que dão 1054 sem ela.
**Na promoção:** conferir em produção (só leitura) `SELECT DISTINCT training_goal FROM exercises` = só `hypertrophy` e
rodar o `replay-dry-run` antes e depois (o `fingerprint` por usuário tem de ser o mesmo).

### 66.3 F3 — editor de Modelo e Modo Treino (2026-10-25)

Pedido do usuário (épico M97): o objetivo aparece nas telas. Regra da F3: **a tela não escreve número, nome nem
literal de objetivo**; tudo chega pronto de `GET /training-goals` (rota de leitura, sem banco).

| Regra | Dono | Consumidores | Proibido fora do dono |
|---|---|---|---|
| Rótulo de cada objetivo (`GOAL_LABELS`, `goal_label`: leitura tolerante, ausente = default) | `api/app/domain/training_goal.py` | `GET /training-goals` (`app/training_goals.py`) | nome do objetivo escrito na tela |
| Defaults por objetivo para a tela: `defaults_for(goal).to_dict()` (reps, RPE, descanso mín/máx/médio, séries e tetos de sanidade: os campos `sets_count`, `rpe_target` e `rest_seconds_sanity_max` reservados na F2 ganham consumidor) e `rest_follows_generation_interval(goal)` (item sem descanso: "da geração" em hipertrofia, o do perfil em força; `item_rest_seconds` passa a usá-lo, mesmo resultado) | `api/app/domain/prescription_defaults.py` | `GET /training-goals` → placeholders do card do item (`lib/training-goals.itemFieldPlaceholders`), descrição do Select "Objetivo", descanso default de linha sem `rest_seconds` no Modo Treino (`goalDefaultRestSeconds`) | reps/RPE/descanso/séries por objetivo escritos no frontend |
| RPE-alvo, teto e técnicas fora da política (`goal_target_rpe`, `goal_rpe_ceiling`, `goal_disallowed_techniques`) | `api/app/domain/training_safety.py` | `GET /training-goals` → aviso no card do item de força com técnica proibida; Modo Treino (normal e Intensivo) não oferece a técnica (`lib/training-goals.techniqueForGoal`, com o objetivo carimbado pelo servidor na linha da ficha) | lista de técnicas por objetivo no frontend |
| Precedência no EDITOR (espelho de `resolve_goal`: override do item > Modelo > default), só para mostrar o default do objetivo efetivo antes de salvar; o valor que vale é o carimbado pelo servidor (`plan_goal_stamp`) | `api/app/domain/training_goal.py` | `frontend/src/lib/training-goals.ts` (`effectiveItemGoal`), paridade em `frontend/src/lib/training-goal.parity.json` gerada por `api/tests/unit/test_m97_f3_objetivo_telas.py --gravar` (o mesmo JSON leva o payload real da rota e os números do deload para os testes do frontend) | resolver o objetivo fora do espelho |
| "Objetivo do dia" no slot: aplica o MESMO override a todos os itens de grupo/equipamento do slot (sem nível novo de dado; cardio fora) | — (ação de edição, sem número) | `SlotEditor` (`applySlotGoal`) | — |

**Telas.** Editor de Modelo: Select "Objetivo" no cabeçalho (o PUT só leva o campo depois de hidratado: sem ele a API
preserva o gravado), override por item (Herdar/cada objetivo) nos cards de equipamento e de grupo muscular, "Objetivo
do dia" no slot e badges. Lista de Modelos e histórico de versões com o badge (a lista de versões passa a devolver
`training_goal`, `training_goal_of` do dono). Modo Treino: badge do objetivo no título do exercício e no briefing.
`lib/workout-plan-payload.ts` ecoa `training_goal` da linha (ausente some do JSON). O timer já usava o `rest_seconds`
prescrito; a linha sem descanso usa o default do objetivo dela.

**Contrato do relógio (Zepp).** O app do relógio não está neste repositório. As rotas `/workout-sessions/*` só
ganharam campos OPCIONAIS (`deload` no corpo do PATCH meta e na resposta, o bloco `deload` do `start` que já vinha da
D) e uma rota nova de leitura (`GET /workout-sessions/{id}/deload`); nenhum campo existente mudou de nome, tipo ou
obrigatoriedade. O relógio herda descanso e objetivo pela ficha (`GET /workout-plans/{id}`, `training_goal` e
`rest_seconds` da linha, desde a F0).

**Impacto em produção.** Toda linha de produção é hipertrofia (medido na F0/F1/F2; a tela só agora oferece força):
- `GET /training-goals` é leitura pura dos donos (sem banco);
- `item_rest_seconds` passa por `rest_follows_generation_interval`, que devolve o mesmo ramo de antes (hipertrofia =
  ponto médio do intervalo pedido); paridade do replay da F1 intacta;
- o editor só envia `training_goal` do cabeçalho depois de hidratado e ecoa o override do item que a API devolveu
  (`null` para todo item atual): a forma canônica das versões não muda e nenhum PUT abre versão nova;
- no Modo Treino, `techniqueForGoal` devolve a técnica da linha para hipertrofia (lista vazia) e
  `goalDefaultRestSeconds` devolve o mesmo default de 60 s;
- SQL tocado: `v.training_goal` no SELECT do histórico de versões (coluna da 093) e, no deload da sessão, um SELECT
  da sessão (com `user_id`) + um COUNT das séries, só quando o PATCH traz `deload` ou na rota nova. Pede `ci-completo`.

### 66.4 F4 — evolução e dashboard por objetivo (2026-10-25)

Decisões do usuário (as mesmas da F1/F2/D): e1RM e recorde ÚNICOS entre objetivos; progressão e comparabilidade
separadas por objetivo; a série de força conta no volume hipertrófico com o peso do motor de volume; o deload conta no
volume e na recuperação, mas fica fora de e1RM, recorde e comparabilidade. A F4 só LÊ: nenhuma regra de prescrição
muda.

| Regra | Dono | Consumidores | Proibido fora do dono |
|---|---|---|---|
| Filtro opcional `?goal=` das leituras que nunca filtraram objetivo (ausente = sem filtro, a resposta de sempre; inválido = 422): `optional_goal_query` | `api/app/training_goal_query.py` (leitura estrita do dono `training_goal`) | `/analytics/exercise-progress`, `/analytics/dashboard-progression` (tabela), `/analytics/muscle-groups-slot-comparison` (`VolumeService.get_recent_exercise_volume`/`get_equivalent_sets_by_activity`), `/analytics/muscle-groups-recent-exercise-volume` e `/analytics/dashboard-progression-suggestion` (ausente = objetivo default do motor; pendências resolvidas abaixo); `/analytics/dashboard-progression-trend` usa o `goal_query` da F1 (ausente = default, o veredito de sempre) | filtro escrito na rota |
| Peso por objetivo DENTRO de uma agregação SQL: `sql_goal_case(alias, objetivo, então, senão)` (valor do vocabulário, números validados) | `api/app/domain/training_goal.py` | `methodology_volume.sql_hypertrophy_volume_weight` | `CASE WHEN <alias>.training_goal = ...` escrito à mão |
| Peso da série de força no volume hipertrófico em SQL: `sql_hypertrophy_volume_weight(alias)` (= `STRENGTH_SET_HYPERTROPHY_VOLUME_WEIGHT`, interpolado; outro objetivo = 1) | `api/app/intelligence/methodology_volume.py` | `muscle_summary_service` (`ROLE_TOTALS_SQL` séries por papel e estímulo, `DAILY_GROUP_TOTALS_SQL`, `GROUP_SESSIONS_SQL`, `GROUP_EXERCISES_SQL`, `session_equipment_sql`), `adherence_service.SESSION_EFFECTIVE_SQL` (estímulo), relatório (`DAILY_GROUP_TOTALS_SQL`) | número do peso em SQL/rota |
| Rótulos: `GOAL_LABELS`/`goal_label` (objetivo, os da F3 §66.3, definição única) e `DELOAD_SOURCE_LABELS`/`deload_source_label` (marca de deload, novos) | `domain/training_goal.py`, `domain/deload.py` | relatório Markdown, payloads do dashboard (`deload_label`, `training_goal_label`); a tela lê os do objetivo de `GET /training-goals` | rótulo escrito no relatório ou na tela |
| Recorde e linha de e1RM do drill-down ÚNICOS: exposições de TODOS os objetivos, sem deload (`EXPOSURES_ALL_GOALS_SQL_TEMPLATE`, `get_exposures_between(all_goals=True)`, `records_all_goals`, `e1rm_series`); chave física da exposição mais recente do objetivo pedido; sessões de deload só marcadas (`DELOAD_SESSIONS_SQL`, `deload_sessions_between`) | `api/app/intelligence/dashboard/exposure_service.py` (e1RM de `domain/e1rm`, chave de `comparability`) | `/analytics/dashboard/exercises/{id}/progress` (`records`, `e1rm_series`, `deload_sessions`, `training_goal`), painel de força | recorde/e1RM filtrado por objetivo |
| e1RM por sessão da tabela de `/dashboard-progression`: a sessão de deload fica FORA (`e1rm = None`), marcada (`deload_source`, `deload_label`); `training_goal` da execução | `analytics._build_progression_sessions` (predicado do dono `deload.is_deload_session`) | `/dashboard/progressao` (gráfico: força destacada, deload como marca) | — |
| Painel de força: compostos principais = `exercise_catalog.is_compound` com mais sessões de FORÇA (sem deload) na janela, até `STRENGTH_PANEL_MAX_EXERCISES` (4); empate = mais recente, depois menor id (`select_main_lifts`); top set = `exposure_snapshot` (carga de topo × mais reps nas séries de topo) das sessões de força; e1RM = linha única | `api/app/intelligence/dashboard/strength_panel.py` | `GET /analytics/dashboard/strength-panel` (`/dashboard/progressao`, visão geral) | seleção/top set na tela |

**Painéis de volume secundários que passam a ponderar a série de força** (pendência da F2; hipertrofia multiplica por 1,
fadiga/dose/séries físicas/tonelagem seguem cheias): tendência semanal por grupo (`direct_sets`/`indirect_sets`/
`stimulus_equivalent_sets`; os campos legados brutos não mudam), ranking de exercícios por grupo, linha do tempo de
sessões (estímulo; fadiga cheia), razão estímulo/fadiga (estímulo), séries equivalentes por atividade e ativação por
atividade (séries e equivalentes; a média de carga segue pelas séries cheias), `muscle_summary_service` e
`adherence_service` (estímulo e séries por papel, no SQL) e `report/daily_breakdown` (detalhe diário por grupo, pela mesma
`DAILY_GROUP_TOTALS_SQL`). As "séries efetivas" do bloco por EQUIPAMENTO do relatório não ponderam (não são volume de um
grupo).

**Marcas de deload** (rótulo do dono): tabela de `/dashboard-progression`, drill-down (`deload_sessions`), linha do
tempo `/dashboard-workout-sessions` (`deload_source`, `deload_label`, `training_goals`), `/analytics/dashboard/workouts`
(`deload_source`, `deload_label`) e a linha do tempo do relatório Markdown ("Sessão (Deload (período))").

**Relatório Markdown.** O bloco por equipamento ganha `goals`/`by_goal` SÓ quando o período tem objetivo diferente do
default (período só de hipertrofia: o dict de sempre, sem chave nova); com MAIS de um objetivo, `by_goal` traz (resumo +
detalhamento por objetivo, cortados das MESMAS linhas do resumo geral); a seção de músculos ganha uma linha com os
objetivos e o peso da série de força (interpolado do dono) só quando o período tem força. Período só de hipertrofia: o
relatório sai idêntico.

**Frontend.** Sem regra numérica nem nome de objetivo: `lib/training-goal-filter.ts` (opções, default e destaque a partir
de `GET /training-goals` via `lib/training-goals.ts` da F3; "Todos" é só texto de UI), `TrainingGoalBadge` na tabela,
`lib/exercise-progress.buildUnifiedE1rmChart`, `lib/progression-chart` (força destacada, deload marcado),
`lib/strength-panel.ts`, `dashboardKeys` com o objetivo. O espelho `motor-constants.generated.json` não muda.

**Guarda.** `strength_panel.py` entra na lista de donos do `objetivo_treino_ramo` (acompanha as sessões de força). As
allowlists não mudam.

**Impacto em produção.** Em produção toda execução é hipertrofia e nenhuma sessão é de deload (F0/D medidos em
2026-10-25; as colunas da 093/094 ainda nem foram promovidas). Com só hipertrofia e sem deload:
- sem `?goal=`, `/exercise-progress`, a tabela de `/dashboard-progression` e o comparativo por slot não ganham filtro
  de objetivo nem parâmetro novo (o SELECT ganha só as colunas aditivas listadas abaixo, que dependem da 093/094) (testes `test_exercise_progress_sem_goal_e_o_sql_de_sempre_e_com_goal_filtra`,
  `test_fetch_set_rows_filtra_objetivo_so_quando_pedido`, `test_series_equivalentes_por_atividade_ponderadas_e_filtro_opcional`);
- o bloco `records` do drill-down é o de antes (as duas leituras trazem as mesmas linhas;
  `test_com_um_objetivo_so_o_bloco_records_e_o_de_antes`);
- os pesos SQL multiplicam por 1 e os pesos Python devolvem as mesmas `units` (testes de igualdade linha a linha da
  linha do tempo, ranking, razão, tendência semanal e séries equivalentes);
- chamada direta da função da rota sem `goal` (scripts, teste de paridade) vale como ausente
  (`training_goal_query.optional_goal_param`), nunca como filtro;
- campos novos são ADITIVOS (`training_goal`, `deload_source`, `deload_label`, `training_goals`, `e1rm_series`,
  `deload_sessions`) e nulos/default.

SQL tocado: `a.deload_source`/`e.training_goal` no SELECT de `_fetch_exercise_set_rows`, `_WINDOW_EXERCISE_SELECT`,
`EQUIPMENT_DAILY_SQL_TEMPLATE` (MAX, já agrupado por `e.id`), `CYCLE_SESSIONS_SQL`, páginas de sessão do
`adherence_service` e da tendência semanal; `e.training_goal` no GROUP BY de `get_equivalent_sets_by_activity` (já agrupa
por `e.id`: o agrupamento não muda); predicado opcional `sql_goal_is` em quatro leituras; uma leitura nova por requisição
no drill-down (exposições de todos os objetivos, sem contexto de fadiga) e uma de sessões de deload; o painel de força faz
1 + até 4 leituras de exposição (todas com `LIMIT` de `MAX_EXPOSURES`). **Pré-requisito:** 093 e 094 aplicadas antes
do código (o mesmo da F1/F2/D): o deploy só migra antes das Lambdas com `RUN_DB_MIGRATIONS=true`; sem as colunas, o
dashboard, o volume e o relatório dão 1054. **Na promoção (só leitura):** `SELECT DISTINCT training_goal FROM exercises`
= só hipertrofia; `SELECT COUNT(*) FROM activities WHERE deload_source IS NOT NULL` = 0; smoke de
`/analytics/dashboard/strength-panel` (`lifts: []`) e do drill-down; `replay-dry-run` antes e depois com o mesmo
`fingerprint`. O `data_quality` do painel de força é só indicador de presença (HIGH com compostos, INSUFFICIENT sem).

**Limites conhecidos (para F5/backlog).** As aplicações de técnica avançada (Fase 5) somadas em Python ao estímulo do
`muscle_summary_service`/`adherence_service` não ponderam o objetivo: a força não prescreve técnica
(`training_safety.safety_for_goal`). A leitura de repetições de `/volume-grupamentos` (`/analytics/training-volume`)
não tem filtro de objetivo.

**Pendências da F4 resolvidas (2026-10-26).**
- ~~`/analytics/dashboard-progression-suggestion` sem `goal`~~: aceita `?goal=` opcional
  (`optional_goal_query` + `optional_goal_param` na rota) e repassa o VALOR a
  `services.suggested_progression_for_mapping(training_goal=)` -> `prescription_service.suggest_for_pair(training_goal=)`
  -> `PairQuery.training_goal` (a mesma da geração de ficha): trilha (`goal_tracks`/`resolve_track`), fase do grupo e
  escada (`ladder_for`) do objetivo, decididas pelo motor. Nenhuma regra na rota. Ausente (HTTP ou chamada direta sem
  `goal`) = `None` = o objetivo default, a resposta de sempre, sem campo novo; com `goal`, a resposta ecoa
  `training_goal` (aditivo). A tela `/dashboard/progressao` manda o objetivo do filtro já existente ("Todos" = sem
  `goal`) e mostra `TrainingGoalBadge` (só objetivo diferente do default); `dashboardKeys.progressionSuggestion` leva o
  objetivo. Teste: `test_m97_f4_sugestao_e_volume_por_objetivo.py` (sem goal = mesma chamada e mesma resposta; força =
  faixa e `engine_version` da escada de força pelo motor real com leituras falsas).
- ~~`/volume-grupamentos` sem seletor de objetivo~~: seletor (`lib/training-goal-filter.sessionGoalFilterOptions`,
  rótulos de `GET /training-goals`, `TrainingGoalBadge`), "Todos" = sem `goal`. Filtra o comparativo por slot
  (`/muscle-groups-slot-comparison?goal=`, da F4) e `/muscle-groups-recent-exercise-volume`, que ganha o mesmo
  `?goal=` opcional repassado a `VolumeService.get_recent_exercise_volume(training_goal=)` (predicado do dono
  `sql_goal_is`, já usado pelo comparativo por slot: nenhum SQL novo). Caminhos e chaves de cache com o objetivo em
  `lib/volume-grupamentos-queries.ts`.

**Impacto em produção (pendências).** Produção só tem hipertrofia (medido na F0/D). Sem `goal` as duas rotas chamam o
motor/serviço exatamente como antes (testes de igualdade da chamada e da resposta, inclusive chamada direta sem `goal`,
como `tests/integration/dataapi/test_rotas_paridade.py`); o frontend só manda `goal` quando o usuário escolhe um
objetivo. SQL: nenhum texto de SQL mudou (o predicado opcional de `get_recent_exercise_volume` já existia).

### 66.5 Carga de força por %1RM e normalização do item na geração (fix M97, 2026-10-26)

Pedido do usuário depois do teste no DEV (Modelo "teste zepp", template 32: itens de força com descanso 10–60 s, reps
10/6-12, RPE 9–10, 2–10 séries e rest-pause/drop/FST-7/GVT). Decisões: (A) toda prescrição de força com e1RM usa a
faixa de %1RM do e1RM ATUAL; (B) o Modelo guarda o que foi digitado e a GERAÇÃO normaliza o item ao perfil do
objetivo, com aviso auditado.

| Regra | Dono | Consumidores | Proibido fora do dono |
|---|---|---|---|
| Carga por %1RM: `STRENGTH_LOAD_PCT_1RM_MIN`/`MAX` (80/85, inteiros), `GoalLadder.load_pct_1rm` (força; hipertrofia `None`), `uses_e1rm_load(goal)`, `e1rm_load_range(e1rm, passo, goal)` → `E1rmLoadRange` (piso arredondado PARA CIMA e teto PARA BAIXO ao passo; passo que não cabe = o múltiplo logo abaixo do ponto da faixa, mínimo um passo; sugerido = ponto `STRENGTH_LOAD_SUGGESTED_FRACTION` (0,5) da faixa crua PARA BAIXO ao passo, dentro da faixa). No `prescribe(..., current_e1rm_kg=)`, com e1RM e carga aplicável: carga = sugerido; decisão de carga pela comparação com a carga da trilha (acima `INCREASE_LOAD`, abaixo `DECREASE_LOAD`, igual/sem carga `HOLD`), sem o bloqueio de janela; evidência `load_regime` (`pct_1rm` \| `double_progression`, só no ramo de força) e `strength_e1rm_load` | `api/app/domain/progression_engine.py` | `generation/deterministic_prescription.prescribe_pairs` (Modos A/B, rascunho e congelamento, `prescription_service`), `domain/prescription.build_prescription` (`strength_load`, aditivo), `exercise_briefing` (`prescription.strength_load`), Modo Treino (`lib/exercise-briefing.formatStrengthLoadRange`: só formata) | percentual de 1RM, regra do sugerido ou arredondamento da faixa fora do dono |
| Platô de força no regime %1RM: o recuo `STRENGTH_PLATEAU_RESET_FACTOR` continua gravado NA TRILHA pelo avaliador (estado da progressão dupla), mas só decide a carga quando não há e1RM; com e1RM, um e1RM estagnado mantém a carga e a queda dele a reduz | `api/app/domain/progression_engine.py` (doc das constantes) | — | recuo aplicado sobre a carga por %1RM |
| e1RM ATUAL do par: o e1RM CONFIÁVEL (`domain/e1rm`) da exposição mais recente do exercício no MESMO equipamento, de todos os objetivos, sem deload, dentro de `CURRENT_E1RM_LOOKBACK_DAYS` (90) dias (`current_e1rm_from_exposures`, `current_e1rm_by_pair`: uma leitura de exposições por exercício, a mesma do recorde, `all_goals=True`, sem contexto de fadiga) | `api/app/intelligence/dashboard/exposure_service.py` | `deterministic_prescription.current_e1rm_for_requests` (só pedidos com `uses_e1rm_load`; falha = sem e1RM, logada `prescription.current_e1rm_unavailable`) | e1RM de referência da força calculado em outro lugar |
| Normalização do item ao objetivo na GERAÇÃO (`normalize_item_for_goal(item, goal)` → item + `GoalAdjustment`s; default = o MESMO objeto, sem ajuste): descanso fora de [mín, máx] do perfil → clamp; reps fora da faixa (`reps_target_fits_goal`, só em série de repetições) → `item_reps_target`; RPE acima do teto (`training_safety.goal_rpe_ceiling`) → `effective_intensity.clamp_rpe_text`; séries acima de `ladder_for(goal).max_sets_count` → o teto (inclusive no Modo B); técnica fora da política (`goal_allows_technique`) → removida com aplicações/duração. Texto do relatório `goal_adjustments_text` (números do perfil interpolados) | `api/app/domain/prescription_defaults.py` | `generation/mode_b.py` (itens do slot ANTES do par e da prescrição; aviso em `volume_planning.warnings`, auditoria `generation_audit.goal_adjustments`), `generation/freeze_mode_b.py` (o item do Modelo da linha, a mesma normalização → `goal_adjustments` + `WARNING_GOAL_NORMALIZED` no `prescription_json`), `generation/mode_a.py` (rede de segurança na linha gerada; a progressão já prescreve no perfil) | ajustar descanso/reps/RPE/séries/técnica de força na rota, no SQL, no validador ou na tela |
| Código de aviso `WARNING_GOAL_NORMALIZED` e campos aditivos `strength_load`/`goal_adjustments` do snapshot (só quando existem) | `api/app/domain/prescription.py` | `lib/workout-plans-types` (rótulo/tooltip do aviso, sem número), Modo Treino | — |
| Deload de força (decisão do Tech Lead, 2026-10-26): a BASE do `DELOAD_LOAD_FACTOR` é o sugerido da faixa %1RM congelada (`snapshot.strength_load`, lido por `progression_engine.e1rm_load_suggested_kg`) quando existe; sem ela, a carga da trilha, como antes | `api/app/domain/deload.py` (`item_deload_prescription`) | `exercise_briefing` (bloco `deload.prescription`), `engine_decision_context.load_deload_prescription` (próxima série no deload) | base de deload de força escolhida fora do dono |
| Sugestão do par com objetivo (`/dashboard-progression-suggestion?goal=strength`, #635): `PairSuggestion.strength_load` (evidência do motor) e `strength_load` na resposta de `services.suggested_progression_for_mapping` só quando existe | `api/app/domain/prescription_service.py` (lê do motor) | `/dashboard-progression-suggestion`, `/exercises/suggested-progression` | — |
| Troca de par no Modo Treino no objetivo do ITEM (M97-D2, decisão do Tech Lead de 2026-10-26: a troca não pode contaminar força com a regra de hipertrofia): `/exercises/suggested-progression?session_id=&workout_plan_exercise_id=&training_goal=`. O objetivo é o RESOLVIDO do item da ficha da sessão (`effective_intensity.plan_item_goal`: snapshot congelado, senão a coluna da 093; leitura `deload_periods.session_plan_item_goal_safe`), senão o do pedido; o default vai ao motor como `None` (`services._engine_goal`), então item de hipertrofia ou pedido sem objetivo = a resposta de sempre | `api/app/domain/training_goal.py` (vocabulário/default) e o motor do par (`prescription_service.suggest_for_pair`) | `services.suggested_progression_for_mapping` (`_item_goal`), Modo Treino (`lib/deload.pairSuggestionPath`, `workout-mode-plan-flow.originalPlanItemId`: só repassam valores) | objetivo da troca decidido na tela; confiar só no objetivo do cliente quando há item da sessão |

**O que a geração faz com o "teste zepp".** Item de força com e1RM: descanso no mínimo da faixa de força, reps do
perfil, RPE no teto, séries no teto, sem técnica, carga = sugerido da faixa (ex.: e1RM 123,33 kg, passo 2,5 →
100–102,5 kg, sugerido 100 kg) e o aviso "ajustado ao objetivo". Sem e1RM: o mesmo, com a carga da trilha. Item com
override de hipertrofia no mesmo Modelo: intocado. Teste ponta a ponta (motor real, leituras trocadas):
`tests/unit/test_m97_forca_carga_1rm_normalizacao.py` (rascunho e congelamento do Modo B). A Lambda
`musclefit-plan-generator` roda a mesma geração (`plan_generation_core.dispatch_plan_generation`).

**Modo Treino.** O briefing mostra "Faixa de carga: 100–102,5 kg (80–85% 1RM)" e o card da série "Faixa: …"
(`formatStrengthLoadRange` sobre o bloco pronto `strength_load`; unidade do usuário; percentuais da API).

**RAG.** Placeholders novos `strength.carga_pct_1rm_min`/`_max` (`rag_placeholders`, de `ladder_for(goal).load_pct_1rm`);
o FOR-000 e o FOR-006 deixam de dizer que o app não prescreve % de 1RM.

**Guarda.** Nenhum padrão novo; allowlists inalteradas. Os percentuais são inteiros (80/85): `0.85` em Python casaria o
padrão `limiar_fadiga` (outra semântica).

**Impacto em produção.** Toda linha de produção é hipertrofia (F0–F4) e o M97 ainda não foi promovido. Com só
hipertrofia, cada ramo novo devolve o de antes: `normalize_item_for_goal` devolve o MESMO item, sem ajuste;
`current_e1rm_for_requests` não lê nada (nenhum pedido de força); `prescribe` ignora `current_e1rm_kg` (teste de
igualdade do `to_dict`); o snapshot não ganha chave nova; a auditoria do rascunho não ganha `goal_adjustments`. Paridade
do replay (`test_m97_f1_paridade_replay.py`): `fingerprint` `c903dbc0…` e decisões `2a5bdda6…` idênticos (o replay
não chama `prescribe`). SQL tocado: só a coluna `i.rest_seconds` (da 058) no SELECT dos itens do slot do
congelamento do Modo B (`freeze_mode_b._plan_context`); a leitura de exposições para o e1RM só roda para itens de
força. Pede `ci-completo`.

## 67. Deload — M97 D (2026-10-25)

Pedido do usuário (25/10): deload = dias em que o usuário reduz RPE, carga e repetições SEM que o motor de progressão
considere regressão; vale para hipertrofia e força. Ativação por PERÍODO ("semana de deload", início/fim) e AVULSA
(marcar a sessão como deload ao iniciar o Modo Treino). Conta na recuperação e no volume semanal real; NÃO entra em
progressão, platô, comparabilidade, e1RM nem recorde. A interface é da F3 (toggle, botão, badge) e da F4 (gráficos).

| Regra | Dono | Consumidores | Proibido fora do dono |
|---|---|---|---|
| Vocabulário da origem (`DeloadSource`: `period`, `manual`; `DELOAD_SOURCE_VALUES`), leitura tolerante da coluna (`read_deload_source`/`deload_source_value`: NULL/desconhecido = sessão normal), "a sessão é de deload?" (`is_deload_session`) e origem carimbada no `start` (`session_deload_source`: período ativo na data da sessão vence a marca avulsa) | `api/app/domain/deload.py` | `workout_sessions.start_session` (carimbo em `activities.deload_source`; sem deload o INSERT é o de sempre), `training_history.SessionRecord.is_deload`, `workout_sessions._evaluate_set`, `volume_service`, `exercise_performance`, `deload_periods.deload_status` | decidir deload na tela, na rota ou no SQL |
| Prescrição de deload (`deload_prescription`): carga = `DELOAD_LOAD_FACTOR` (0,60) × carga de trabalho da TRILHA, no passo do equipamento (`progression_engine.round_to_equipment_step`); RPE-alvo `DELOAD_TARGET_RPE` (6); reps = PISO da faixa do item (`training_history.parse_reps_range`; sem faixa, o piso de `prescription_defaults.defaults_for(objetivo).reps_target`: 8 em hipertrofia, 3 em força); séries reduzidas em `DELOAD_SETS_REDUCTION_FRACTION` (1/3, redução arredondada meio para cima), mínimo `DELOAD_MIN_SETS` (1) | `api/app/domain/deload.py` | `item_deload_prescription` no próprio dono (precedência: trilha > `suggested_load_kg` > `load_target` congelados > coluna da ficha; séries/faixa/objetivo do snapshot, senão da ficha), chamado pelo briefing do Modo Treino (bloco `deload.prescription`) | fator, RPE ou redução de séries escritos fora do dono (guarda `deload_fator`) |
| Próxima série numa sessão de deload (`deload_next_set_load`): executada acima da carga de deload -> REDUZIR para ela; senão MANTER (motivo `next_set_load.REASON_DELOAD`, rótulo `deload_reason_label` com os números interpolados). O status de esforço da série é cobrado contra `DELOAD_TARGET_RPE` (`deload_effort_target`; o alvo do protocolo só vale para técnica em `DELOAD_ALLOWED_TECHNIQUES`, hoje nenhuma, §67.2), que é também o `rpe_target_effective` gravado | `api/app/domain/deload.py` | `engine_decision_context.next_set_load_for` (com `PairEngineContext.deload`, montado por `load_deload_prescription`: a trilha só é lida na sessão de deload), chamado por `workout_sessions._evaluate_set` (salvamento da série) e por `workout_ai_generation_core` (avaliação por IA, via `load_pair_context`) | regra da próxima série de deload fora do dono |
| Período: default de uma semana (`DELOAD_PERIOD_DEFAULT_DAYS` = 7), teto `DELOAD_PERIOD_MAX_DAYS` (14); fim antes do início ou já terminado (fuso do usuário) = 422 (`validate_period`); sobreposição (`periods_overlap`); dia/janela em deload (`active_on`, `overlaps_window`, `deload_ranges`) | `api/app/domain/deload.py` | `/deload-periods` (`app/deload_periods.py`: criar, listar, `current`, cancelar; outro período não cancelado sobreposto = 409 `deload_period_overlap`, checado com a linha do usuário travada) | — |
| A progressão PULA a sessão de deload. No `finish` nenhum objetivo é avaliado e nada é gravado (`GoalEvaluation.skipped_reason` = `SKIP_REASON_DELOAD`, no resumo do `finish`). No replay a sessão fica na janela SEM execução para a progressão (`SessionRecord.progression_view`): não avança, não regride, não conta para platô (nem o de força), não muda trilha nem fase. O teto de ativação lê a sessão inteira (o volume conta) | `api/app/domain/progression_evaluator.py` (predicado do dono `deload`) | `progression_state.run_session_evaluation`/`rebuild_for_user`, `exercise_briefing` (última execução) | avaliar sessão de deload |
| Exclusão em SQL: `sql_not_deload(alias)` / `sql_is_deload(alias)` (`<alias>.deload_source IS [NOT] NULL`) e `sql_period_overlaps(alias)` (período não cancelado que toca `(fim, início)`) | `api/app/domain/deload.py` | `exposure_service` (`EXPOSURES_SQL_TEMPLATE`, `BATCH_EXPOSURES_SQL_TEMPLATE`: comparabilidade, veredito, recorde/e1RM do drill-down, desempenho do briefing), `session_records.HISTORY_SQL_TEMPLATE` (recorde/progressão por sessão), `services.get_last_session_sets` (sessão anterior, recorde e e1RM do Modo Treino), `analytics._fetch_group_e1rm_rows` (desempenho por grupo), `context_service.load_deload_ranges`, `deload_periods` | `deload_source IS [NOT] NULL` escrito à mão (guarda `deload_filtro_sql`, allowlist vazia) |
| Recuperação e volume: a sessão de deload CONTA 100% (dose, fadiga, contexto de fadiga prévia, volume semanal, ativação). Fica fora só do e1RM de REFERÊNCIA das outras sessões (`volume_service._compute_session_dose_context`; o ranking `is_new` continua contando a sessão de deload como ocorrência do exercício, porque ela aconteceu) e do sinal "Desempenho recente" (`exercise_performance._last_exposures_window`) | `api/app/domain/deload.py` (`is_deload_session`) | `volume_service`, `recovery_read_model` (`a.deload_source` no histórico empacotado), `exercise_performance` | — |
| Insights em silêncio no deload: "volume abaixo da meta" quando a régua das semanas (`insight_rules.below_min_ruler_start`) toca um deload (`suppresses_below_min_volume`); regressão (`EXERCISES_REGRESSING`, `PERFORMANCE_DROP_WITH_CONTEXT_EVENT`) enquanto o deload está em curso no fim da janela (`suppresses_regression`) | `api/app/domain/deload.py` | `insight_rules` (`InsightContext.deload_ranges`), `overview_service` (`context_service.load_deload_ranges` desde o início da régua) | — |

**Onde o dado mora (migração 094).** A tabela `deload_periods (id, user_id -> users ON DELETE CASCADE, starts_on DATE,
ends_on DATE, created_at, cancelled_at NULL)` tem o índice `idx_dp_user_period (user_id, starts_on, ends_on)`. A coluna
`activities.deload_source ENUM('period','manual') NULL` entra por ADD COLUMN INSTANT (NULL = sessão normal, todo dado
existente). Sem FK para `exercise_catalog`. A origem é CONGELADA no `start`: cancelar um período não desmarca as
sessões já iniciadas nele. O teste `test_migration_094_plano.py` prova que o ENUM da migração é o
`DELOAD_SOURCE_VALUES` do dono.

**API.**
- `POST /workout-sessions/start` aceita `deload: true` (avulso) e devolve o bloco `deload` (`active`, `source`,
  `period`). Um período ativo na data da sessão carimba `period`. A data é a do fuso do usuário: o `local_date` do
  payload ou o dia de `started_at` na parede do usuário.
- `GET /workout-plans/{plan}/exercises/{item}/briefing?session_id=` devolve `deload` (a origem gravada na sessão; sem
  `session_id`, o período ativo hoje) e, quando ativo, `deload.prescription`.
- `GET /deload-periods/current` devolve o deload de hoje e as constantes do dono (a tela não escreve número).

**Guarda.** Dois padrões novos, ambos com allowlist vazia: `deload_filtro_sql` (SQL; só Python de `api/app`) e
`deload_fator` (atribuição de número a nome `deload*` em `api/app` e `frontend/src`).

**Espelho do frontend.** `motor-constants.generated.json` (seção `next_set_load`) ganha o motivo `NEXT_SET_DELOAD` e o
rótulo dele, gerados por `test_m76_lote1b_espelhos_fe.py --gravar`.

**Impacto em produção.** Nenhuma sessão existente é de deload (coluna nova, NULL) e nenhum período existe, então todo
ramo novo devolve o valor de antes:
- sem deload, `SessionRecord.progression_view` é o `only_goal` de sempre, e `evaluate_*_by_goal` saem iguais;
- paridade do replay (`test_m97_f1_paridade_replay.py`): `fingerprint` `c903dbc0…` e decisões `2a5bdda6…` IDÊNTICOS aos
  da F1/F2;
- os filtros `sql_not_deload` são verdadeiros para toda linha atual (exposições, recordes, e1RM e sessão anterior
  iguais);
- o `start` sem deload grava o MESMO INSERT, e a próxima série sem deload segue a regra de sempre, sem leitura extra;
- os insights só silenciam com `deload_ranges` não vazio.

SQL tocado nos caminhos quentes:
- `a.deload_source` no SELECT de `SESSIONS_SQL`, do histórico do overview (`_FULL_HISTORY_EXERCISE_SELECT`,
  `HISTORY_PACKED_SQL`) e de `_session_exercise_row` (JOIN de `activities` pela FK da linha). No `HISTORY_PACKED_SQL` a
  coluna entra no GROUP BY por sessão, que já agrupa por `a.id`, então o agrupamento não muda;
- os predicados `IS NULL` nas exposições, recordes, e1RM por grupo e sessão anterior;
- leituras novas por requisição: uma de período no `start` e no briefing, duas no overview. Todas são tolerantes
  (falha = sem deload).

**Pré-requisito (o mesmo da F1/F2):** a 094 aplicada no Aurora ANTES do código. O deploy roda `migrate` antes das
Lambdas (gate `RUN_DB_MIGRATIONS=true`); sem a coluna, as leituras de `a.deload_source` dão 1054. O ADD COLUMN usa
`ALGORITHM=INSTANT`. Na promoção, medir `INNODB_TABLES.TOTAL_ROW_VERSIONS` de `activities` (teto 64): se faltar
margem, a migração falha sem aplicar nada e o deploy para antes das Lambdas.

**Na promoção:** conferir (só leitura), logo depois da 094, que `deload_periods` está vazia e que
`SELECT COUNT(*) FROM activities WHERE deload_source IS NOT NULL` = 0. Rodar o `replay-dry-run` antes e depois: o
`fingerprint` por usuário tem de ser o mesmo.

**Limites conhecidos (para F3/F4).**
- (Resolvido na F3, §67.1.) ~~A marca avulsa só existe no `start`.~~
- A importação do relógio e o `POST /activities` não carimbam deload (NULL = sessão normal).
- Algumas leituras de gráfico/painel ainda mostram a sessão de deload sem marca: a tabela de
  `/analytics/dashboard-progression`, a tendência semanal e a linha do tempo. O destaque e a retirada das linhas de e1RM
  são da F4. **Resolvido na F4 (§66.4)** (tabela, drill-down, linha do tempo e `/dashboard/workouts`; a tendência
  semanal agrega por semana e não marca sessão).
- A geração da ficha não aplica a prescrição de deload no congelamento: a ficha do dia continua a de sempre, e o deload
  chega pelo briefing e pela próxima série (a F3 mostra).

### 67.1 F3 — deload nas telas e numa sessão já iniciada (2026-10-25)

Decisão do Tech Lead (F3): ligar/desligar o deload de uma sessão JÁ iniciada é permitido SOMENTE enquanto ela não
tem série gravada.

| Regra | Dono | Consumidores | Proibido fora do dono |
|---|---|---|---|
| Trava e nova origem (`DELOAD_TOGGLE_MAX_SAVED_SETS` = 0, `can_toggle_deload`, `toggled_deload_source`: pedido igual ao estado atual = idempotente, mesmo com série; mudança só sem série, senão `DeloadToggleLocked` -> 409 `deload_locked`; ligar segue `session_deload_source` (período ativo na data da sessão vence a marca avulsa); desligar = NULL, inclusive numa sessão carimbada pelo período) | `api/app/domain/deload.py` | `PATCH /workout-sessions/{id}/meta` (`deload: bool`, ausente/null = não mexe; resposta com o bloco `deload` + `can_toggle`), `GET /workout-sessions/{id}/deload` (banner do Modo Treino) | contar série ou decidir a trava na rota ou na tela |

**Telas.** Início do Modo Treino (`DeloadStartPanel`): explicação com os números de `/deload-periods/current`,
toggle "Sessão de deload" (vai como `deload: true` no `start`; com período ativo fica ligado e travado) e "Iniciar
semana de deload" (duração default do dono) / "Encerrar semana de deload" (`/deload-periods`). Execução
(`DeloadSessionBanner`): banner quando a sessão é de deload, com o toggle até a 1ª série. Briefing: pedido com
`?session_id=` (a origem gravada na sessão) e o bloco "Deload hoje" com a prescrição pronta (`deload.prescription`).
Os textos interpolam os números da API (`lib/deload.ts`); nada de deload é escrito no frontend (guarda `deload_fator`).

**Impacto em produção.** Nenhuma sessão existente é de deload. O PATCH meta sem `deload` é o de sempre (nenhuma leitura
nova; teste `test_meta_sem_deload_nao_le_nem_grava_deload`). A rota nova é só leitura e filtra pelo usuário (sessão
alheia = 404).

### 67.2 Fix — deload desde a 1ª série e sem técnica (2026-10-26)

Bugs do teste no DEV (sessão iniciada como "Sessão de deload"): (1) a 1ª série de cada exercício vinha com a carga
cheia, porque o Modo Treino tira a carga/reps/séries/RPE da 1ª série da ficha (`GET /workout-plans/{id}`), que não
sabia da sessão; só a 2ª série caía pela regra `NEXT_SET_DELOAD`; (2) o Modo Treino oferecia técnica avançada
(drop set, rest-pause, Modo Intensivo) na sessão de deload.

| Regra | Dono | Consumidores | Proibido fora do dono |
|---|---|---|---|
| Item da ficha como a sessão de deload o executa (`deload_plan_item`, overlay de LEITURA sobre `item_deload_prescription`, a mesma base da próxima série): carga (`load_target`/`suggested_load_kg`), reps no piso, séries reduzidas, RPE-alvo `DELOAD_TARGET_RPE` (nunca acima do teto exibido), sem a faixa %1RM (`strength_load` = null), cargas de aquecimento/ajuste sobre a âncora de deload (`session_loads.session_load_plan`), bloco `deload` no item. Valor ausente na prescrição de deload mantém o do item | `api/app/domain/deload.py` | `deload_periods.apply_session_deload_to_plan` (lê a origem da sessão e a trilha de cada par), chamado por `GET /workout-plans/{id}?session_id=` (Modo Treino: `lib/deload.workoutModePlanPath`; o toggle do banner invalida `WORKOUT_MODE_PLAN_QUERY_ROOT`) | carga/alvo de deload calculado na tela, na rota ou no SQL; gravar o overlay na ficha |
| Par TROCADO no Modo Treino numa sessão de deload (M97-D2, 2026-10-26): a sugestão do par novo desde a 1ª série (`deload_pair_suggestion` sobre `pair_deload_prescription`, a mesma precedência de `item_deload_prescription` com a sugestão do par novo no lugar do snapshot: faixa %1RM do par novo em força com e1RM > trilha do par novo no objetivo do item > carga sugerida). Carga (`load_target`/`suggested_load_kg`), reps no piso da faixa sugerida (sem faixa, a do objetivo), RPE-alvo `DELOAD_TARGET_RPE` (nunca acima de `effective_rpe_ceiling`), sem `strength_load`, nota `deload_reason_label`, bloco `deload`. Séries: as da ficha da sessão, já reduzidas por `deload_plan_item` (sem corte duplo; `deload.sets_count` = null). Técnica: a do item já sai nula da ficha da sessão | `api/app/domain/deload.py` | `deload_periods.apply_session_deload_to_pair_suggestion` (fail-open, logado `deload.pair_suggestion_overlay_failed`) sobre a base ÚNICA `deload_periods.pair_deload_prescription_for` (trilha do par novo + sugestão do par novo), chamado por `services.suggested_progression_for_mapping(session_id=, workout_plan_exercise_id=, item_training_goal=)` <- `GET /exercises/suggested-progression` (Modo Treino: `lib/deload.pairSuggestionPath`, na troca antes da 1ª série e na continuação sintética; o toggle do banner invalida `PAIR_SUGGESTION_QUERY_ROOT`). Séries seguintes: `engine_decision_context.load_deload_prescription(plan_row=)` com `deload.pair_swapped` (par lançado ≠ par planejado do item) usa `services.swapped_pair_deload_prescription`, a MESMA base (falha = a base de antes, logado `engine_decision_context.swapped_pair_deload_failed`) | carga/alvo de deload do par trocado calculado na tela; base da próxima série do par trocado tirada do snapshot do item original |
| Técnicas permitidas na sessão de deload: `DELOAD_ALLOWED_TECHNIQUES` (vazio: nenhuma), `deload_allows_technique`; `deload_effort_target(protocolo, técnica)`: o alvo do protocolo só vale para técnica permitida (hoje nunca) | `api/app/domain/deload.py` | ficha da sessão (técnica, aplicações, duração, parâmetros e cargas de aplicação = null), briefing (`advanced_technique` = null), `POST /workout-sessions/{id}/exercises/{eid}/sets` (pedido com `rpe_target_source='protocol'` em deload é cobrado e gravado contra o RPE-alvo do deload, origem gravada NULL), `goal_prompt_context.deload_section` (linha "NÃO sugira nenhuma técnica", `DELOAD_PROMPT_VERSION` = `deload-v2`) | lista de técnicas do deload no frontend ou no prompt |

**A ficha congelada não muda**: o overlay vale só na resposta com `session_id` de uma sessão de deload; ligar o deload
pelo PATCH meta antes da 1ª série muda a próxima leitura (a origem é relida a cada pedido).

**Guarda.** Nenhum padrão novo; allowlists inalteradas (a constante nova mora no dono).

**Impacto em produção.** Nenhuma sessão de produção é de deload e o M97 ainda não foi promovido. Sem `session_id`, a
rota da ficha é a mesma (nenhuma leitura nova; teste `test_rota_da_ficha_sem_sessao_nao_abre_transacao`); com
`session_id` de sessão normal, uma leitura da origem (`activities` pela PK) e a ficha INTACTA (o mesmo objeto; teste
`test_sessao_normal_devolve_a_ficha_intacta`). A avaliação de série sem deload é a mesma (o protocolo segue valendo;
teste `test_sessao_normal_mantem_o_protocolo`). Prompts sem deload: os mesmos textos e versões. Paridade do replay
inalterada (o replay não passa por nada disto). SQL: nenhum SQL novo (reusa `session_deload_source_safe` e
`TRACK_WORKING_LOAD_SQL`, uma leitura de trilha por item só na sessão de deload). Custo novo: com `session_id`, uma
transação a mais (o SELECT da origem pela PK) ao abrir a ficha no Modo Treino. Falha no overlay = a ficha de sempre
(fail-open, logado `deload.plan_overlay_failed`; teste `test_falha_no_overlay_devolve_a_ficha_de_sempre`).

**Troca de par (M97-D2, 2026-10-26; resolve o limite registrado no #637).** Antes, o item com o par trocado nesta
sessão usava a sugestão cheia do par novo na 1ª série e só a 2ª caía pela `NEXT_SET_DELOAD`, e a 2ª ainda partia do
snapshot do item ORIGINAL (ex.: hack 80 kg trocado por leg press: a 2ª série caía para 60% do hack, 48 kg). Agora o
Modo Treino pede `/exercises/suggested-progression` com `session_id`, o item trocado e o objetivo dele; numa sessão de
deload a 1ª série já vem com a prescrição de deload do par novo, e as séries seguintes partem da MESMA base
(`deload.pair_swapped` na regra da próxima série). Fora do deload, a troca de um item de força sai no objetivo do item
(§66.5). Testes: `test_m97_d2_deload_troca_de_par.py`.

**Impacto em produção (M97-D2).** O M97 ainda não foi promovido (#626–#638 só na `develop`): nenhuma sessão de
produção é de deload e todo item é de hipertrofia (F0–F4).
- Sem `session_id` (dashboard, `/planos-treino`, treino livre): nenhuma leitura nova e a resposta de sempre; item de
  hipertrofia ou pedido sem objetivo também (o default vai ao motor como `None`; testes
  `test_pedido_sem_objetivo_ou_de_hipertrofia_e_identico`, `test_sem_sessao_nenhuma_leitura_nova`).
- Com `session_id` de sessão normal de hipertrofia: a leitura da origem (`activities` pela PK) e a do objetivo do item
  (`SESSION_PLAN_ITEM_GOAL_SQL`, pela PK do item e da sessão), resposta IDÊNTICA
  (`test_sessao_normal_hipertrofia_devolve_a_resposta_identica`).
- Na sessão de deload, uma leitura de trilha a mais (`TRACK_WORKING_LOAD_SQL`). No salvamento de série de um par
  TROCADO numa sessão de deload, a sugestão do motor para o par novo é recalculada (a mesma da 1ª série: trilhas, logs
  do exercício, prontidão projetada); par planejado ou sessão normal: nada novo.
  Custo aceito (decisão do Tech Lead: a 2ª série parte da mesma base da 1ª): o mesmo `suggest_for_pair` da rota da
  troca, ou seja, as leituras do motor do par na transação do salvamento mais as transações Data API próprias de
  `list_exercise_logs` e `current_readiness` (só SELECT, sem lock; enxergam o estado anterior à série, a mesma base da
  1ª série). Só roda em série de par TROCADO numa sessão de deload; exceção = a base de antes (timeout do Data API não
  é capturado: medir a latência do salvamento nesse caso no smoke da promoção).
- Ordem de deploy: os SELECTs de `_session_exercise_row` e `IN_PROGRESS_SNAPSHOT_SQL` rodam em TODA série e passam a
  ler `wpe.training_goal` (093); já liam `a.deload_source` (094) desde a F1/F2, então o pré-requisito é o mesmo do
  §67: 093/094 aplicadas no Aurora ANTES das Lambdas (o deploy roda `migrate` antes, gate `RUN_DB_MIGRATIONS=true`).
  Sem isso, o salvamento de série de todo treino daria 1054, como já daria pela 094.
- SQL tocado: `SESSION_PLAN_ITEM_GOAL_SQL` (novo, só leitura) e três colunas a mais (`wpe.mapping_id`,
  `wpe.preferred_equipment_id`, `wpe.training_goal`) no SELECT de `workout_sessions._session_exercise_row` e de
  `engine_decision_context.IN_PROGRESS_SNAPSHOT_SQL`. Pede `ci-completo`.
- Paridade do replay inalterada (o replay não passa pela sugestão do par nem pela regra da próxima série).

## 68. Objetivo FORÇA no RAG, nos prompts e na geração de Modelo por IA — M97 F5 (2026-10-25)

Pedido do usuário (épico M97): o material de força (documento enviado, chunks FOR-001..052) entra no RAG só quando o
objetivo é força; os prompts de IA do treino ganham persona/regras por objetivo e sabem quando a sessão é de deload; a
geração de Modelo por IA aceita objetivo do Modelo e objetivo por dia (Modelo híbrido, ex.: 2 dias de força em 5).
Regra da F5: **nenhum número de prescrição escrito à mão em RAG ou prompt** (princípio 4); tudo interpolado dos donos.

| Regra | Dono | Consumidores | Proibido fora do dono |
|---|---|---|---|
| Pasta do RAG próprio de cada objetivo (`GOAL_RAG_FOLDERS`, `goal_rag_folder`; hipertrofia = `None`, a base de sempre) | `api/app/domain/training_goal.py` | `rag_chunking.goal_rag_files` | pasta de objetivo escrita no consumidor |
| Seleção do RAG por objetivo: `select_rag_text(..., training_goal=)` (objetivo ou coleção; o material do objetivo ABRE o texto), `select_goal_rag_text` (chunks filtrados por grupo como a base, teto `MAX_RAG_GOAL_CHARS`), `rag_base_limit(goal)` (teto da base + o do objetivo; default = `MAX_RAG_BASE_CHARS`) | `api/app/rag_chunking.py` | `periodization/context.load_rag_text`, `insights_core.select_insights_rag` | injetar o material de objetivo por outro caminho; `.md` de objetivo na raiz de `api/privado/rag/markdown` |
| Interpolação dos placeholders `{{<objetivo>.<campo>}}` do RAG: reps/RPE/descanso/séries (`defaults_for`), RPE-alvo/teto/técnicas fora (`training_safety`), RIR (`e1rm.rir_from_rpe`), piso de exercícios e compostos do dia (`periodization/limits`). Placeholder desconhecido: estrito nos testes, aviso em produção | `api/app/rag_placeholders.py` | `rag_chunking.select_goal_rag_text` | número de prescrição no Markdown de `OBJETIVOS/` (guarda `faixa_reps_prosa` cobre a pasta) |
| Texto de prompt por objetivo e por deload: foco da persona (`focus_text`: "hipertrofia" no default, letra por letra), bloco do objetivo não-default e do deload (números de `defaults_for`, `training_safety`, `deload.deload_reason_label`/`DELOAD_SETS_REDUCTION_FRACTION`; a prescrição de deload do item vem pronta de `PairEngineContext.deload`), versão com sufixo só nos ramos novos (`prompt_version`: `+goal-v1`, `+deload-v1`) | `api/app/goal_prompt_context.py` | `workout_summary_ai`, `exercise_set_evaluation_ai`, `exercise_summary_ai`, `insights_prompt` | ramo por objetivo no prompt; número de objetivo/deload escrito no texto |
| Compostos pesados do dia por objetivo (`STRENGTH_DAY_HEAVY_COMPOUNDS_MIN/MAX` = 2/4, FOR-042; `heavy_compound_range`), objetivo do item no dia (`day_item_goal`: composto = o do dia; isolador num dia de força = hipertrofia, FOR-023) e piso de exercícios pelo objetivo DECLARADO do dia (`slot_session_goal`) | `api/app/periodization/limits.py` | `periodization/goal_plan`, `validation`, `context` (prompt), `rag_placeholders` | faixa de compostos ou objetivo do isolador decididos no validador ou no prompt |
| Alvo de reps cabe na faixa do objetivo? (`reps_target_fits_goal`; hipertrofia aceita qualquer alvo) | `api/app/domain/prescription_defaults.py` | `validation._apply_goal_plan` (`GOAL_REPS_FIXED`) | comparar faixa de reps no validador |
| Objetivo por dia na geração por IA: objetivo do Modelo e do dia (`day_goal`, `declared_day_goal`), plano não-default? (`has_goal_plan`), contexto do prompt (`plan_for_prompt`), alvo e override do item (`item_target_goal`, `override_for`) | `api/app/periodization/goal_plan.py` | `context.build_context`/`goal_plan_section`, `validation._apply_goal_plan`/`_check_methodology` | — |

**RAG.** `rag/markdown/OBJETIVOS/FORCA/TREINO_FORCA_HIPERTROFIA_RML_RAG.md`, curado a partir do documento do usuário:
- os números operacionais viraram placeholders (faixa de reps, RIR/RPE, descanso, séries, compostos, técnicas);
- o volume semanal remete à meta do macro-grupo (como HIPERTROFIA.md desde a v8 da periodização);
- frequência e "uma semana a cada N semanas" ficaram qualitativas: não têm dono no app, e os dias de força são os que o
  usuário distribui;
- citação científica que NÃO é prescrição do app fica atribuída: o ACSM 2026, "a partir de cerca de 80% de 1RM". O app
  não prescreve % de 1RM, e o texto diz isso. (Mudou no fix de 2026-10-26, §66.5: a força prescreve a faixa de %1RM
  do e1RM, interpolada por placeholder.) É o mesmo padrão de HIPERTROFIA.md, que mantém "em torno de 80% da força
  máxima" como descrição;
- RML saiu dos chunks injetados (seção 8, cabeçalhos `###`): fica como contexto conceitual, sem número;
- os templates de resposta e o "prompt sugerido" do documento saíram, porque são instruções que competiriam com os
  prompts do app;
- o chunk novo FOR-000, "Como o app prescreve força", traz todos os números interpolados.

O material inteiro tem ~27 mil caracteres (~7 mil tokens) e só entra nos prompts de força.

**Prompts.** O objetivo e o deload chegam prontos:
- resumo do treino: `exercises.training_goal` e `activities.deload_source` do `activity_detail` (`SELECT *`);
- avaliação de série: `PairEngineContext.training_goal`/`.deload` (`load_pair_context`);
- resumo do exercício: `e.training_goal` e `a.deload_source` no SELECT de `services.exercise_summary_context` (colunas
  da 093/094; o ramo sem usuário passa a fazer JOIN de `activities`, que a FK garante);
- Insights: `insights_core.load_window_goal_context` (DISTINCT `e.training_goal` da janela +
  `context_service.load_deload_ranges`), tolerante (falha = default).

No deload, o prompt diz que carga, reps e RPE baixos são o objetivo da sessão, não regressão.

**Periodização por IA (`PROMPT_VERSION` = `periodization-v12`; desde a M98 C5, `periodization-v13`, §69.7 — com objetivo e sem cardio pedido o texto é o desta seção, letra por letra).** `PeriodizationDraftIn.training_goal` e `day_goals`
(um por dia; 422 fora do vocabulário ou com tamanho diferente de `days_per_cycle`). Com plano não-default:
- o prompt ganha a seção OBJETIVO POR DIA, o catálogo traz `is_compound` (`context.load_compound_flags`, uma leitura de
  `exercise_catalog`) e o RAG traz o material de força;
- o validador (`_apply_goal_plan`, antes da segurança e de novo depois do complemento, idempotente) grava o override
  por item. O isolador fora do objetivo do dia leva override EXPLÍCITO (`ISOLATION_GOAL_OVERRIDE`);
- o validador tira técnica fora da política (`TECHNIQUE_REMOVED`, reason `TECHNIQUE_NOT_ALLOWED_FOR_GOAL`), corta o RPE
  no teto do objetivo (`RPE_CLAMPED`, reason `TRAINING_GOAL`) e ajusta reps fora da faixa (`GOAL_REPS_FIXED`);
- avisa compostos do dia fora da faixa (`DAY_HEAVY_COMPOUNDS_OUT_OF_RANGE`) e usa o piso de exercícios do objetivo do
  dia;
- o complemento de volume não acrescenta técnica fora do objetivo do item e conta o descanso do objetivo, inclusive o
  do Modelo.

O Modo A determinístico já consumia `defaults_for(goal)` por grupo (F2). A tela (`PeriodizationModal`) ganha "Objetivo
do modelo" e "Objetivo por dia", com rótulos de `GET /training-goals`. O corpo do pedido só leva os campos quando o
usuário os escolhe, e o rascunho leva `training_goal` para o builder.

**Guarda.** `faixa_reps_prosa` passa a varrer `rag/markdown/OBJETIVOS/`. Nenhum padrão novo e allowlist inalterada. Os
módulos novos não têm ramo `TrainingGoal.STRENGTH` (`objetivo_treino_ramo`): o ramo mora nos donos (`limits`,
`training_goal`).

**Impacto em produção.** Hipertrofia (todo o histórico e todo pedido atual) sai byte a byte igual:
- `select_rag_text` com objetivo default ou `None` devolve o mesmo texto, com o mesmo teto e sem leitura nova
  (`test_m97_f5_rag_objetivo.py`);
- os prompts de hipertrofia sem deload são idênticos e mantêm a versão. Os snapshots `.txt` de
  `test_llm_prompt_renderizado_snapshot.py` não mudaram; só o `index.json` da periodização mudou, pelo
  `prompt_cache_key` da v12. O dedupe do resumo de treino (`input_sha256`) não regenera nada;
- a periodização sem objetivo tem o mesmo prompt, catálogo e rascunho da v11; só o `prompt_version` do log e do cache
  é novo.

Leituras novas:
- `exercise_summary_context`: duas colunas no SELECT;
- Insights: três SELECT por pedido (os objetivos da janela e os dois de `load_deload_ranges`), tolerantes;
- periodização com plano não-default: uma leitura de `exercise_catalog` no contexto e uma no validador.

O bucket do RAG publica a pasta nova pelo `fileset("**/*.md")` de `infra/s3_rag.tf`, no pipeline.

**Quem é afetado hoje.** Só quem usar força ou deload. Até a promoção do lote M97 (F0–F5), produção não tem as colunas
da 093/094, e nenhum Modelo, linha ou sessão de força ou deload existe (F0/F1/D, medido em 2026-10-25). Os ramos
`+goal-v1`/`+deload-v1` e o RAG de força nascem sem usuário.

**Pré-requisito (o mesmo da F1/F2/D):** a 093 e a 094 aplicadas no Aurora ANTES do código. O SELECT de
`exercise_summary_context` lê `e.training_goal` e `a.deload_source` sem tolerar a coluna ausente (dá 1054); o deploy
roda `migrate` antes das Lambdas (`RUN_DB_MIGRATIONS=true`). Na promoção, conferir em produção (só leitura), logo depois
da 094: `SELECT training_goal, COUNT(*) FROM exercises GROUP BY 1` = só `hypertrophy`, e
`SELECT COUNT(*) FROM activities WHERE deload_source IS NOT NULL` = 0.

**Número em prosa no material do objetivo.** O único número fora de placeholder é a citação atribuída de % de 1RM do
ACSM (a faixa que o app prescreve, desde o §66.5, é placeholder do dono `progression_engine`). A guarda `faixa_reps_prosa` não cobre percentual: um % de prescrição
novo nesse material é achado de revisão (`guardiao-motores`).

## 69. Motor de cardio — M98 C0, fundação (2026-10-26)

Plano: [`docs/cardio/PLANO_MOTOR_CARDIO_2026-10-26.md`](../cardio/PLANO_MOTOR_CARDIO_2026-10-26.md) §4 e §5 (C0).
Premissas confirmadas pelo usuário: "recuperação" = desaquecimento; tempos do HIIT em SEGUNDOS, o resto em
minutos; sem equipamento.

**Donos novos (Python puro, constantes nomeadas):**

| Dono | O que mora nele |
|---|---|
| `api/app/domain/cardio_prescription.py` | modos `continuous` (Normal) e `hiit`; campos por modo e obrigatórios; `validate` (zona alta > baixa, mínimos/tetos por campo em `FIELD_LIMITS`, teto da sessão `CARDIO_SESSION_MAX_MINUTES`, mensagem de sempre "item de cardio exige cardio_duration_minutes > 0."); `DEFAULTS` do editor (Normal: o 1º degrau da escada de Z2 de `cardio_policy`; HIIT: o protocolo preferencial de `cardio_policy`); `total_seconds` (aquecimento + parte principal + recuperação; item antigo = minutos × 60); `describe` (texto canônico); `normalize_fields` (o que vai para a coluna) e `canonical_extras` (forma canônica da versão); `draft_cardio` (cardio do rascunho da ficha) |
| `api/app/domain/hr_zones.py` | vocabulário Z1–Z5; precedência Intervals → FCmáx manual → sem zonas (`resolve_athlete_zones`); fallback `MAX_HR_ZONE_BOUNDS_PERCENT` (50/60/70/80/90/100% da FCmáx, aprovado pelo usuário em 27/10/2026); FCmáx manual aceita 100–230 bpm (aprovado em 27/10/2026); `zone_of_hr`. A leitura das zonas do Intervals e o mapeamento 7 → 5 entraram na C2 (§69.3) |

**Consumidores ligados na C0:**

| Consumidor | Consome |
|---|---|
| `workout_plan_templates._validate_template_payload` | `cardio_prescription.validate` (422 com o nome do slot) |
| `periodization/time_estimate.estimate_slot_time` (+ preview `slot_times[]`) | `cardio_prescription.total_seconds`; a constante `SECONDS_PER_CARDIO_MINUTE` saiu |
| espelho `frontend/src/lib/template-time-estimate.ts` | `lib/cardio-prescription.ts` (espelho do tempo, sem constante própria: bloco `cardio_prescription` do `motor-constants.generated.json`); casos de cardio Normal/HIIT/sem modo/incompletos em `template-time-estimate.parity.json`, gerados pelo dono |
| `template_versions` (INSERT, cópia, leitura e comparação) | `normalize_fields` na gravação; `canonical_extras` na comparação — Modelo com cardio antigo (com ou sem o backfill) tem a forma canônica de antes da 095 |
| rascunho da ficha (Modo A, Modo B, legados e dia de descanso) | `draft_cardio`: `cardio_minutes` (total) e `cardio_prescriptions` (cada item, com o texto) |
| save da ficha (`lib/workout-plan-payload.buildDraftRawPayload`) | leva `cardio_minutes` e `cardio_prescriptions` no `raw_payload` (a ficha não tem coluna de cardio) |
| relatório Markdown (`markdown_report_service` → `markdown_renderer`) | `describe` e `slot_cardio_minutes` |
| editor do Modelo (`workout-plan-template-form.ts`, cards de cardio) | duração inicial (`DEFAULTS`) e mínimo/teto (`FIELD_LIMITS`) pelo JSON gerado; repassa os campos novos no PUT (replace-all) |

**Lacuna do rascunho confirmada e corrigida.** Antes da C0: o Modo A e o Modo B devolviam `cardio_minutes: None`
no dia de treino; o dia de descanso pegava só a duração do 1º item; o `WorkoutPlanIn` não tem o campo, e o save
descartava o que viesse. O cardio do Modelo nunca chegava à ficha. Agora o rascunho traz o cardio estruturado e o save
grava no `raw_payload` (coluna JSON que já existe; sem migração em `workout_plans`).

**Migração 095** (`095_prescricao_cardio.py`): 9 colunas anuláveis em `workout_plan_template_slot_items` num ALTER
INSTANT só, sem FK, e backfill `cardio_mode='continuous'` nos itens de cardio com duração (idempotente: só
`cardio_mode IS NULL`). A coluna `max_hr_bpm_manual` do perfil (plano §5, C0) NÃO entrou: a FCmáx manual ainda não tem
tela nem leitor; `hr_zones` recebe o valor como argumento. (Entrou na C1, migração 096: §69.1.)

**Aprovados pelo usuário em 27/10/2026 (constantes nomeadas nos donos; antes provisórios):** os percentuais do fallback por FCmáx
(`hr_zones.MAX_HR_ZONE_BOUNDS_PERCENT`); os tetos/mínimos de `cardio_prescription.FIELD_LIMITS` e
`CARDIO_SESSION_MAX_MINUTES`; os padrões sem origem em `cardio_policy` (`DEFAULT_WARMUP_MINUTES`,
`DEFAULT_COOLDOWN_MINUTES`, `DEFAULT_CONTINUOUS_TARGET_ZONE`, `DEFAULT_HIIT_LOW_ZONE`, `DEFAULT_HIIT_HIGH_ZONE`).

**Ordem para a C1:** o PUT do Modelo é replace-all. Um cliente que não conhece os campos novos regrava um item HIIT
como Normal. Por isso o editor com HIIT (C1) só pode subir depois deste frontend (que repassa os campos,
`pickCardioExtras`) estar em produção.

**Guarda:** `cardio_prescricao_paralela` nasce vazia (as cópias saíram neste PR: `<= 0` da rota e do editor,
`minutos × 60` do `time_estimate` e do espelho, `min={1}` e o `20` do editor); `zonas_fc_paralelas` nasce com
`rag/markdown/CARDIO.md` (15), fora do índice do RAG, e zera na C3.

**Impacto em produção (medido em 2026-10-26, só leitura no Aurora):** 82 itens de cardio em
`workout_plan_template_slot_items` (de 1.355), todos com duração (20–40 min), 1 por slot; 32 nas versões vigentes de
8 Modelos (7 em slot de descanso); nenhum item que não é cardio com duração. O backfill toca as 82 linhas. Todos são
Normal sem aquecimento/recuperação/zona: o tempo estimado do slot fica idêntico (paridade antiga verde sem regerar
os totais) e a forma canônica também (nenhum PUT abre versão por causa da 095). `TOTAL_ROW_VERSIONS` da tabela = 8;
o ALTER único leva a 9 (limite 64). Pré-requisito: a 095 no Aurora ANTES do código (o SELECT das versões lê as
colunas novas sem tolerar a ausência; o deploy roda `migrate` antes das Lambdas com `RUN_DB_MIGRATIONS=true`).

### 69.1 M98 C1 — editor do Modelo (Normal/HIIT) e FCmáx manual (2026-10-26)

Plano §C1. Nenhuma regra nova fora dos donos; os valores da C0 (tetos, padrões, % da FCmáx; aprovados em 27/10/2026, §69.8) não mudaram.

**O que entrou nos donos:**

| Dono | Novo |
|---|---|
| `cardio_prescription.py` | `describe_short` (resumo do item recolhido: "HIIT 8× 30 s Z4 / 90 s Z2 · 5+5 min", "Normal 20 min Z2 · 5+5 min"); `validation_error` (a mensagem de `validate` ou None); `zone_fields` no export do JSON |
| `hr_zones.py` | `validate_manual_max_hr` (escrita da FCmáx do perfil: inteiro em `MANUAL_MAX_HR_MIN_BPM..MANUAL_MAX_HR_MAX_BPM`, senão `HrZonesError`; `None` limpa); `athlete_zones_payload` (forma da API); `manual_max_hr_bpm` no export do JSON |

**Consumidores:**

| Consumidor | Consome |
|---|---|
| `CardioPrescriptionEditor.tsx` (cards `EquipmentSeriesItemCard` e `PercentBudgetItemCard`) | seletor Normal/HIIT e campos por modo, rótulos com unidade, mínimos/tetos e padrões: JSON gerado; tempo total e texto: espelho com paridade; mensagem de erro: `POST /workout-plan-templates/cardio-prescription-check` (o dono, a mesma regra do PUT); faixa de bpm das zonas: `GET /users/me/hr-zones`. Sem equipamento |
| item recolhido (os dois cards) | `cardioShortDescribe` (espelho de `describe_short`, paridade em `cardio-prescription.parity.json`) |
| `lib/workout-plan-template-form.newItem` e troca de tipo para cardio | `newCardioPrescription` = modo padrão do dono com os seus `DEFAULTS` |
| troca de modo no editor | `cardioModeChangePatch`: os `DEFAULTS` do novo modo, os campos do outro modo vazios |
| `PUT /users/me`, `PUT /users/{id}` | `validate_manual_max_hr` (400 `invalid_max_hr_bpm_manual`); só com o campo no payload, `null` limpa |
| `GET /users/me/hr-zones` | `resolve_athlete_zones` (Intervals ainda `None`, C2) + `athlete_zones_payload` |
| perfil (`PerfilClient.tsx`) | campo FCmáx com mínimo/teto do JSON (`hr_zones.manual_max_hr_bpm`) |

**Migração 096** (`096_fcmax_manual_perfil.py`): `users.max_hr_bpm_manual SMALLINT NULL`, ALTER INSTANT, sem FK, sem
índice, sem backfill; idempotente; `down` = DROP COLUMN se existir.

**Retrocompatibilidade:** item de cardio antigo (sem modo) abre como Normal (`cardioMode`) e volta no PUT com o modo
vazio, como veio: a forma canônica é a de antes e o save sem mudança não abre versão. O editor não grava nada ao abrir.

**Guarda:** sem allowlist nova; `cardio_prescricao_paralela` segue vazia e `zonas_fc_paralelas` com `CARDIO.md` (15).

**Impacto em produção (medido em 2026-10-26, só leitura no Aurora):** última migração aplicada = 094 (a 095 da C0 e a
096 sobem juntas); `users.max_hr_bpm_manual` ausente; `users` com 4 linhas (3 ativas) e `TOTAL_ROW_VERSIONS` = 5 (a 096
leva a 6, limite 64); 82 itens de cardio, todos antigos (sem `cardio_mode`): abrem como Normal e o save sem mudança não
abre versão. Ordem: a C1 só sobe na mesma promoção da C0 (ou depois), com `RUN_DB_MIGRATIONS=true`.

### 69.2 M98 C2 (correção da C1) — zonas do editor do Modelo por papel (2026-10-26)

Decisão do usuário: o editor do Modelo mostra as zonas de FC de quem vai treinar. **De quem** são as zonas é regra de
AUTORIZAÇÃO, só no backend (`api/app/athlete_hr_zones.py`, sem número):

| Papel | Zonas mostradas |
|---|---|
| admin | as do usuário logado |
| aluno (`regular`) | as próprias, sempre (nunca as de terceiros, mesmo pedindo o Modelo de outra pessoa) |
| personal trainer | as do aluno com atribuição VIGENTE do Modelo (`workout_plan_template_assignments.unassigned_at IS NULL`); sem Modelo (novo), sem aluno ou com mais de um aluno vigente: nenhuma (`subject.kind='none'` + `reason`), nunca as do próprio PT |

| Consumidor | Consome |
|---|---|
| `GET /workout-plan-templates/hr-zones?template_id=N` (`me_router`, exige sessão) | `resolve_template_zones_subject` + `load_athlete_zones` (leitura) + `hr_zones.athlete_zones_payload`; devolve também `subject` |
| `GET /users/me/hr-zones` (perfil) | `load_athlete_zones` (a mesma leitura; antes a rota lia a coluna por conta própria) |
| `CardioPrescriptionEditor.tsx` | `useTemplateHrZones(templateId)` em `lib/hr-zones.ts`: só pede as zonas do Modelo aberto e exibe o aviso de `subject` (`hrZonesHint`, texto) |

Testes: `test_template_hr_zones_autorizacao.py` (PT só vê aluno vinculado; admin vê as suas; aluno não vê terceiros).

**Impacto em produção (medido em 2026-10-26, só leitura):** nenhum usuário ativo com papel `personal_trainer` (1
admin, 2 `regular`); 3 Modelos com atribuição vigente, cada um com UM aluno (nenhum com mais de um). Para o admin e o
aluno nada muda (continuam vendo as próprias zonas); o ramo do PT não tem usuário hoje.

### 69.3 M98 C2 — realizado no Intervals: zonas, tempo em zona e aderência (2026-10-26)

Plano §C2. Os valores da C0/C1 (tetos, padrões, % da FCmáx; aprovados em 27/10/2026, §69.8) **não mudaram**.

**Formato real do Intervals** (confirmado no `raw_json` de produção e na OpenAPI `https://intervals.icu/api/v1/docs`):
`GET /athlete/{id}` devolve `WithSportSettings.sportSettings[]` com `id`, `types`, `hr_zones` (int[]: o LIMITE SUPERIOR
de cada zona em bpm; o último é a FCmáx), `hr_zone_names`, `lthr` e `max_hr`; cada atividade traz `icu_hr_zones` (o
mesmo array do esporte na data) e `icu_hr_zone_times` (segundos por zona, na mesma ordem). O padrão do Intervals é o
modelo de 7 zonas por LTHR (Z1–Z4, Z5a, Z5b, Z5c). No usuário 1: `[129, 137, 145, 153, 157, 162, 170]`, LTHR 154.

**O que entrou nos donos:**

| Dono | Novo |
|---|---|
| `hr_zones.py` | `INTERVALS_ZONE_MAP` (7 zonas: `(1, 2, 3, 4, 5, 5, 5)` — Z5a/Z5b/Z5c somam na Z5 do app; 5 zonas: 1 para 1; outra quantidade = sem zonas do Intervals); `INTERVALS_Z1_LOWER_PERCENT` (= o início da Z1 do fallback, 50% da FCmáx: o Intervals não tem limite inferior); `INTERVALS_REFERENCE_SPORT_TYPES` (Run, VirtualRun, Walk, Ride, VirtualRide; senão o 1º esporte com zonas válidas); `zones_from_intervals`, `app_zone_seconds`, `parse_intervals_zone_seconds`, `pick_reference_sport`; `resolve_athlete_zones(..., intervals_max_hr_bpm)` |
| `cardio_prescription.py` | `hiit_phase_seconds` (tempo prescrito por fase do HIIT; `main_seconds` passou a somá-lo, mesmo valor) e `field_value` |
| `cardio_adherence.py` (**novo**, Python puro) | métricas e status da aderência (tabela abaixo) e `summarize_weeks` (semana começa na segunda, data local) |
| `intervals_hr_match.py` | `eligible_candidates(rows, accept=...)`: o MESMO critério de horário (janela de 60 min, desempate total) aceita outro tipo; default continua `WeightTraining` |

**Aderência (`cardio_adherence.evaluate`), aprovada pelo usuário em 27/10/2026 (90%/70%, 50%, janela de 60 min do casamento):**

| Métrica | Regra |
|---|---|
| tempo cumprido | `moving_time` da atividade casada / `cardio_prescription.total_seconds` (aquecimento + principal + recuperação) |
| Normal: zona-alvo | segundos na zona-alvo; `target_zone_share_pct` = % do tempo realizado; `target_zone_completion_pct` = % da parte principal prescrita (limitado a 100) |
| HIIT | zona ALTA ou acima vs `séries × tempo alto`; da zona BAIXA até abaixo da alta vs `séries × tempo baixo` (os dois limitados a 100) |
| status | `adherent`: tempo ≥ `ADHERENT_TIME_MIN_PCT` (90) e zona (alvo/alta) ≥ `ADHERENT_ZONE_MIN_PCT` (70) quando há zona e tempo em zona; `partial`: tempo ≥ `PARTIAL_TIME_MIN_PCT` (50); `low`: abaixo; `not_done`: nenhuma atividade de cardio casada |

O tempo em zona do Intervals é da atividade inteira (sem fases): por isso a zona é medida contra a parte principal.

**Casamento planejado × realizado** (`cardio_adherence_service`): o planejado é o 1º item de cardio do slot da sessão de
força (ficha → `template_id` + `template_slot_sequence`, versão de `plan_version_expr`, o mesmo vínculo da aderência
da força); o realizado é a atividade do Intervals que `intervals_activity_kind` diz ser cardio, escolhida por
`intervals_hr_match.match_session` ancorado no FIM da sessão de força (`activities.ended_at`). Não há terceiro
critério. Cardio em dia de descanso (sem sessão de força): ligado em 27/10/2026 pela ficha salva para a data (§69.8).

**Consumidores e I/O:**

| Consumidor | Consome |
|---|---|
| `intervals_client.fetch_athlete` | `GET /athlete/{id}` (1 chamada a mais por usuário por sync) |
| `intervals_store.upsert_activities` | grava `hr_zone_seconds` (`hr_zones.parse_intervals_zone_seconds` do `icu_hr_zone_times`); ausente não apaga o gravado (`COALESCE`) |
| `intervals_store.upsert_athlete_hr_zones` / `read_athlete_hr_zones` | tabela `intervals_athlete_hr_zones`, crua; payload sem esporte válido não apaga nada |
| `intervals_sync._sync_one_user` (Lambda `musclefit-intervals-sync`) | busca as zonas junto das atividades; falha só das zonas não derruba o usuário (`athlete_zones_failed`) |
| `athlete_hr_zones.load_athlete_zones` (`/users/me/hr-zones`, `/workout-plan-templates/hr-zones`) | `pick_reference_sport` + `zones_from_intervals` → precedência do dono |
| `GET /integrations/intervals/cardio-adherence?window=` (api-core) | `cardio_adherence_service.build_adherence`; janela no fuso do usuário (`time_windows.user_timezone`/`resolve_window`), sem `CURDATE()` |

**Migração 097** (`097_intervals_zonas_fc.py`): tabela `intervals_athlete_hr_zones` (sem FK; UNIQUE `(user_id,
sport_settings_id)`), `intervals_activities.hr_zone_seconds JSON NULL` (ALTER INSTANT) e backfill a partir do
`raw_json` (`icu_hr_zone_times` quando é array; só linhas NULL). Idempotente (convergida = 0 statements), forma
divergente = erro antes de qualquer statement; `down` = DROP COLUMN + DROP TABLE do que existir.

**Guardas:** `cardio_paralelo` (allowlist `intervals_integration.py` 5, `analytics.py` 2; zera na C4) e
`aderencia_cardio_paralela` (vazia). Nenhum uso novo de `LIKE '%cardio%'`.

**Impacto em produção (medido em 2026-10-26, só leitura no Aurora):** 541 atividades em `intervals_activities` (1
usuário); 537 com `icu_hr_zone_times` em array de 7 (o backfill toca essas 537); as 336 que `intervals_activity_kind`
classifica como cardio têm todas tempo em zona mapeável 7 → 5. 1 usuário ativo com Intervals configurado (1 chamada
extra por sync). 8 sessões com cardio prescrito no slot (09–26/10): 5 `adherent`, 2 `partial`, 1 `low` na simulação
com os limiares provisórios.

### 69.4 M98 C3a — agente de cardio v3: JSON estruturado, envelope e RAG (2026-10-26)

Plano §C3, dividida em C3a (este PR, só backend) e C3b ("aplicar no Modelo" num rascunho e exibição no Modo Treino).
Os valores de C0–C2 (tetos, padrões, % da FCmáx, mapeamento 7 → 5, limiares da aderência; aprovados em 27/10/2026, §69.8) **não mudaram**.

**Dono novo `api/app/domain/cardio_envelope.py`** (Python puro). Não coube em `cardio_policy`: o envelope precisa de
`cardio_prescription` (padrões, tempo, validação), que já importa `cardio_policy` — ficaria um ciclo de import.

| Regra | Valor (constante; aprovado pelo usuário em 27/10/2026, com as mudanças de §69.8) |
|---|---|
| Horizonte da recomendação | 7 dias a partir da data de referência (`RECOMMENDATION_HORIZON_DAYS`) |
| HIIT na véspera do dia de perna | vetado (`REASON_EVE_OF_LEG_DAY`); **desde 27/10/2026 o próprio dia de perna permite HIIT, depois da força** (`REASON_LEG_DAY` não veta mais; o dia sai marcado em `DayEnvelope.leg_day`; até 27/10 vetava o dia e a véspera); perna = Quadríceps, Posterior de coxa, Glúteos, Adutores (`LEG_MUSCLE_GROUPS`; Panturrilha fora). Não havia dono prévio de "grupos de perna" (as raízes de `rag_chunking` só buscam chunks de RAG): o conjunto nasce aqui |
| Sessões intensas por semana | até 2 (`WEEKLY_HIIT_MAX`): contam as recomendadas, o HIIT do plano em dia sem recomendação e as intensas já feitas (≥ 300 s em Z4+, `HIGH_INTENSITY_*`) |
| Faixa pelo TSB | **desde 27/10/2026** `ok` ≥ -25 (`HIIT_MIN_TSB`); `loaded` < -25: sem HIIT, zona máx. Z3, 150 min/semana; `high_risk` < -35 (`TSB_HIGH_RISK_BELOW`): sem HIIT, zona máx. Z2, 60 min/semana; `ok`/`unknown`: Z5, 240 min/semana (`BAND_*`). Até 27/10: cortes -20/-30 |
| Recuperação ruim | HRV do último dia ≤ linha de base − 15% (`HRV_DROP_BAD_PCT`), FC de repouso ≥ linha de base + 5 bpm (`RESTING_HR_RISE_BAD_BPM`) ou grupo de perna `NO_DIRECT_WORK`; linha de base = média dos 7 dias anteriores com ≥ 3 pontos; veta HIIT nos 2 primeiros dias (`RECOVERY_SIGNAL_DAYS`) |
| Por dia / por semana | 1 sessão por dia; 3–6 sessões por semana (`WEEKLY_CARDIO_SESSIONS_MIN` só orienta; o máximo corta) |
| Contínuo | parte principal até 60 min (`CONTINUOUS_SESSION_MAX_MINUTES`) |
| Projeção do plano | ficha salva para a data > dia da semana do slot (se TODOS os slots têm) > sequência do Modelo a partir do próximo slot (`resolve_next_slot`) |

**Depois do LLM (`apply_envelope`, o envelope vence):** fora do horizonte e 2ª sessão do dia são cortadas; HIIT vetado
ou acima do teto semanal vira Normal (`DEFAULTS` do Normal, aquecimento/recuperação mantidos); zona acima da máxima do
dia é rebaixada; contínuo acima do teto é limitado; sessão acima dos tetos semanais de sessões/minutos é cortada. Cada
corte/ajuste vai para `rejected`/`adjustments` com o código.

**Validação da saída pelo dono:** `cardio_prescription.clamp_fields` (novo) leva número fora de `FIELD_LIMITS` ao limite
(registrado como `clamped`); `validate` rejeita o resto (obrigatório ausente, zona alta ≤ baixa, teto da sessão, modo
desconhecido) com a mensagem do dono.

**Consumidores e I/O:**

| Consumidor | Consome |
|---|---|
| `cardio_agent_ai` (`cardio-agent-v3`) | `call_openai_structured` com o schema strict de `cardio_recommendation_v3.llm_schema`; o prompt interpola `cardio_policy`, `cardio_prescription` (campos, `FIELD_LIMITS`, `CARDIO_SESSION_MAX_MINUTES`, padrões por `describe(default_prescription(modo))` — até 2026-10-27 era `describe(DEFAULTS[...])`, que tirava as séries do HIIT, §69.7), `cardio_envelope` e `hr_zones`; `envelope` no contexto; `prompt_cache_key` ligado (instruções + RAG = prefixo fixo) |
| `cardio_recommendation_v3` (contrato) | validação pelo dono, `apply_envelope`, `plan_link` (vínculo com o Modelo para a C3b, que grava SEMPRE no rascunho do Modelo, `PUT /workout-plan-templates/{id}/draft`, nunca na versão publicada; slot por `slot_sequence`, item por `item_order`) e o markdown legado gerado do JSON |
| `intelligence/cardio_context_v3` | força (grupos, séries, volume, RPE), prontidão por grupo, cardio realizado de `intervals_activities` (`intervals_activity_kind`, `hr_zones.app_zone_seconds`, `is_high_intensity`), aderência (`cardio_adherence_service`), wellness/sono/PAI, zonas (`athlete_hr_zones`), próximos dias do Modelo ativo; janelas no fuso do usuário (`_user_today`), sem `CURDATE()` |
| `rag_chunking.select_cardio_rag_text` | 21 chunks de `CARDIO.md` (`CARDIO_AGENT_CHUNK_IDS`), só a seção "Conteúdo", placeholders `{{cardio.*}}` interpolados (`rag_placeholders`); `CARDIO.md` continua fora da descoberta automática da geração de ficha (`EXCLUDED_FILES`) |
| `rag_placeholders` | bloco `cardio.*`: zona-alvo e aquecimento do Normal, degrau inicial e faixa de Z2, passo de progressão, protocolo de HIIT, teto do contínuo, frequência e HIIT por semana |
| `POST /integrations/intervals/sync` (sync manual) | `fetch_athlete` + `upsert_athlete_hr_zones` com a tolerância do sync diário (`athlete_hr_zones.failed`), timeout próprio de 5 s e pulada (`skipped`) se o sync já passou de 20 s (teto de 29 s do API Gateway) — correção da C2 |

**Migração 098** (`098_recomendacao_cardio_estruturada.py`): `cardio_recommendations.recommendation_json JSON NULL` e
`prompt_version VARCHAR(40) NULL` num ALTER INSTANT, sem FK/índice/backfill. As linhas antigas ficam NULL e seguem
legíveis pelo markdown. A listagem lê só `prompt_version`; o detalhe devolve o JSON como objeto.

**RAG:** `CARDIO.md` v1.1 sem número à mão de prescrição: "65% a 75% da FCmáx" virou a zona-alvo do sistema em bpm;
"20 a 40 minutos", "até 60", "5 em 5", "3 vezes", "5 a 6 vezes", "10 a 15 minutos" e "2 a 3 minutos" de HIIT viraram
placeholders dos donos. Divergência resolvida a favor do motor: a fonte dizia HIIT de 10–15 min com 2–3 min de
aquecimento; o sistema prescreve o protocolo de `cardio_policy` e o aquecimento padrão de `cardio_prescription`.

**Guardas:** `zonas_fc_paralelas` zerou (`CARDIO.md` 15 → 0); novas `envelope_cardio_paralelo` (vazia) e
`numeros_rag_cardio` (vazia, só `CARDIO.md`).

**Impacto em produção (medido em 2026-10-26, só leitura no Aurora):** 27 recomendações em `cardio_recommendations` (1
usuário; 1 nos últimos 30 dias), `context_snapshot` médio de 211 KB, `TOTAL_ROW_VERSIONS` = 0 (a 098 leva a 1). Última
chamada do v2 (`llm.usage`, 18/10): 94.995 tokens de entrada e 816 de saída (≈ US$ 0,075). O v3 renderiza 31–56 mil
caracteres nos casos congelados (v2: 268–295 mil), ≈ 10–19 mil tokens, ≈ US$ 0,012–0,022 por chamada; o prefixo fixo
(≈ 13,8 mil caracteres) é cacheável. Simulação do envelope nos 60 dias até 26/10 (usuário 1, Modelo 36: perna na terça
e no sábado): faixa `ok` em 56 dias e `loaded` em 4; queda de HRV em 3 dias; em média 3 dos 7 dias do horizonte
permitem HIIT; as sessões intensas realizadas foram 1–2 por semana (nunca acima do teto 2). Hoje (TSB -25,4) a faixa é
`loaded`: sem HIIT.

**Aprovados pelo usuário em 27/10/2026:** todos os valores da tabela acima (com os cortes de TSB e a regra de perna de §69.8) e `cardio_policy.CONTINUOUS_PROGRESSION_STEP_MINUTES` (5).

### 69.5 M98 C3b — "Aplicar no Modelo" (rascunho) e cardio no Modo Treino (2026-10-26)

Consome o contrato da C3a (§69.4) sem regra nova de cardio: nenhum limite, padrão, zona ou tempo nasce aqui.

**Dono `cardio_prescription` (funções novas, sem número novo):** `describe_steps` (etapas para exibir:
aquecimento, parte principal — Normal com tempo e zona-alvo, HIIT com séries × alta/baixa e as zonas —, recuperação;
rótulos em `STEP_LABELS`), `total_minutes`/`minutes_label`/`minutes_from_seconds` (o "N min no total" da recomendação e do Modo Treino, um arredondamento só) e `same_prescription` (mesma prescrição
normalizada, aquecimento/recuperação ausentes = 0; é o que torna o "aplicar" idempotente).

| Consumidor | Consome |
|---|---|
| `cardio_recommendation_v3.build_view` / `describe_notes` | `GET /cardio-recommendations/{id}` devolve `recommendation_view`: data rotulada, `prescription_text`, tempo total (via `total_seconds` gravado), justificativa, sinais, `CODE_LABELS`/`REASON_LABELS` dos códigos do envelope, `applicable` (sessão com `plan_link` e quem pede edita o Modelo). O markdown legado usa o mesmo `describe_notes`. Recomendação antiga: `null` (a tela mostra o markdown) |
| `POST /cardio-recommendations/{id}/apply` → `workout_plan_templates.apply_cardio_sessions_to_draft` → `template_drafts.apply_cardio_sessions` (puro) | grava **só no rascunho** (`template_drafts.save_draft`), nunca na versão publicada; slot por `slot_sequence`; `replace` troca o 1º cardio do slot (`item_id` confere na mesma versão), `add` acrescenta no fim; prescrição validada por `cardio_prescription.validate`; idempotente por `same_prescription`; conflito (cardio já alterado no rascunho, slot com outro cardio, cardio ausente) recusa a sessão com mensagem; 409 no pedido inteiro se o Modelo não é o ativo do dono da recomendação, está inativo, ou a recomendação é antiga/sem Modelo. Papel: `require_admin_or_pt` (a regra do `PUT /draft`) |
| `GET /workout-plans/{id}/cardio` → `cardio_display.slot_cardio_for_plan` | Modo Treino ao encerrar a força: cardio do slot da ficha (`template_slot_sequence`) na versão VIGENTE do Modelo (`get_template_if_assigned`), com `describe_steps`, `describe`, `total_minutes` e a faixa de bpm de cada zona das zonas resolvidas do atleta logado (`athlete_hr_zones.load_athlete_zones` → `hr_zones`; só busca pelo número da zona). Só exibição: sem registro nem timer (realizado pelo Intervals; execução guiada = M98-R) |
| Frontend (`components/cardio/CardioRecommendationPanel.tsx`, `modo-treino/CardioAfterStrength.tsx`, `lib/cardio-recommendation-apply.ts`) | só exibe o que a API devolve; nenhum número, limite, rótulo de código ou faixa calculada na tela |

**Guarda:** nenhuma allowlist mudou. A guarda do rascunho (`test_template_drafts.test_nenhum_leitor_oficial_le_o_rascunho`)
continua com os mesmos dois leitores: a rota da recomendação chama `workout_plan_templates`, que usa `template_drafts`.

**Pendente (fora da C3b):** o contexto v2 (relatório e `/analytics/cardio-agent-context`) ainda usa `CURDATE()`.
Resolvido na C4 (§69.6).

### 69.6 M98 C4 — dashboard de acompanhamento e uma leitura só do Intervals (2026-10-27)

Plano: [`docs/cardio/PLANO_MOTOR_CARDIO_2026-10-26.md`](../cardio/PLANO_MOTOR_CARDIO_2026-10-26.md) §C4. Nenhuma regra
numérica nova (o hub consome os donos e o frontend só desenha), mas há mudança de semântica medida abaixo.

| Consumidor | Consome |
|---|---|
| `intelligence/dashboard/cardio_service.build_cardio` → `GET /analytics/dashboard/cardio` | **`weeks`**: minutos por zona Z1–Z5 por semana (soma de SEGUNDOS de `hr_zones.app_zone_seconds`, convertida uma vez por `cardio_prescription.minutes_from_seconds`), contínuo × intensa (`cardio_envelope.is_high_intensity`; sessão sem tempo em zona fica `unclassified`, nunca contínua por eliminação), semana de `cardio_adherence.week_start`. **`adherence`**: `cardio_adherence_service.build_adherence` (status e limiares do dono `cardio_adherence`, rótulo `STATUS_LABELS`), só com Intervals configurado. **`wellness`**: CTL/ATL/TSB, HRV, FC de repouso do cache `intervals_wellness` (TSB ausente por `intervals_store.derive_form_tsb`). **`pai`**: `manual_effort_metrics.pai_score` + meta `cardio_policy.PAI_TARGET_MIN`. **`next_session`**: última recomendação com `recommendation_json` → `cardio_recommendation_v3.next_session` (1ª sessão com data ≥ hoje LOCAL do usuário). **`zones`**: `athlete_hr_zones` → `hr_zones` (origem `intervals`/`manual_max_hr`/`none`). Cada bloco degrada sozinho (`_optional_block`: ressalva, nunca 500). Métricas `cardio-weekly-zones-v1` e `cardio-adherence-v1` (`metrics_registry`). Custo: +7 consultas fixas, nenhuma por item. `tracking=False` (relatório Markdown) pula os blocos e as consultas |
| Frontend `dashboard/cardio/DashboardCardioClient.tsx`, `CardioCharts.tsx`, `lib/dashboard-cardio.ts` | só forma linhas de gráfico, rótulos (`zoneSourceLabel`, cor do status) e o texto acessível dos gráficos; nenhum número, limiar, zona ou classe calculada na tela |
| `analytics.py` (`/analytics/summary` → `cardio_activities`, `/analytics/cardio`) | "o que é cardio" pelo dono `intervals_activity_kind` sobre `activities.activity_type` + `name`, em Python; o SQL agrega por (tipo, nome) — e por período, com o rótulo de `_period_expr` — para o volume lido não crescer com a janela (limite de 1 MB da Data API); o Python só decide a classe de cada grupo e junta os de cardio (FC média = soma ÷ contagem das medidas). Sem `LIKE` |
| `intelligence/cardio_context_service.py` (contexto v2: relatório e `/analytics/cardio-agent-context`) | janelas a partir de `IntelligenceServiceBase._days_ago` (hoje no fuso do usuário), sem `CURDATE()` |

**Uma leitura só do Intervals.** Opção escolhida: **migrar e remover**. `GET /integrations/intervals/cardio-dashboard`
(ao vivo, com `_is_cardio_activity`/`_CARDIO_PATTERNS`, `_normalize_activity`, `_group_by_day`, `_group_by_type` e
`_wellness_trend`) saiu de `intervals_integration.py`; o único consumidor era a tela `/cardio`, que virou redirect para
`/dashboard/cardio` (301 na CloudFront Function `spa-index-rewrite` + `LegacyPathRedirect` de fallback; a rota continua
existindo para favoritos). O item "Cardio" do menu principal saiu (o de "Dashboard > Cardio" fica).

**Dia de descanso (decidido pelo usuário em 27/10/2026, ligado no §69.8).** Sem sessão de força não há âncora de
horário. Regra, no dono do casamento: `intervals_hr_match.match_local_day` — âncora = dia LOCAL inteiro; candidatos = cardio
(`intervals_activity_kind`) com `start_time` no dia; um → `MATCHED`; vários → `MATCHED_AMBIGUOUS` com o mais longo
(desempate: mais cedo, `external_id`). Só vale em dia sem sessão de força. O slot de um dia passado vem da ficha
salva para a data; sem ficha, o dia não é avaliado (§69.8). O dashboard declara essa ressalva
(`REST_DAY_ADHERENCE_LIMITATION`).

**Mudanças de semântica (Impacto em produção no PR da C4):** (1) `cardio_activities` de `/analytics/summary` e os
itens de `/analytics/cardio` passam a usar o critério do dono (força vence; nome e tipo juntos; lista de padrões do
dono) em vez da lista de `LIKE`; (2) as janelas do contexto v2 passam a contar do dia LOCAL do usuário (antes, dia
UTC: diferença de um dia entre 21h e 0h em São Paulo); (3) `/integrations/intervals/cardio-dashboard` e a tela
`/cardio` saíram (redirect). Medição em DEV (cópia de produção do usuário 1, 2026-10-27): 87 sessões em
`activities`, todas `musculacao` e nenhuma com nome de cardio → 0 cardio pelos dois critérios. Medição em produção:
pendente (SSO expirado no momento do PR; SELECTs no PR).

**Guardas:** `cardio_paralelo` **zerada** (`intervals_integration.py` 5 → 0, `analytics.py` 2 → 0). Nenhuma outra
allowlist mudou.

### 69.7 M98 C5 — periodização (geração de Modelo por IA) prescreve cardio (2026-10-27)

Plano: [`docs/cardio/PLANO_MOTOR_CARDIO_2026-10-26.md`](../cardio/PLANO_MOTOR_CARDIO_2026-10-26.md) §C5.
`PROMPT_VERSION` = **`periodization-v13`**. Nenhuma regra numérica nova fora dos donos: os números novos do
envelope do Modelo moram em `domain/cardio_envelope.py`; `periodization/cardio_plan.py` é só colagem.

| Peça | Dono / consumidor |
|---|---|
| Quando o Modelo leva cardio | **Dono do gatilho: `periodization/cardio_plan`** (`cardio_requested`, `CARDIO_REQUEST_RE`; regra não numérica, fora da guarda; o agente de cardio não tem vocabulário paralelo porque sempre roda): `PeriodizationDraftIn.include_cardio` (opcional; `True` = sim, `False` = nunca) vence; ausente = só se as `instructions` falarem de cardio (`CARDIO_REQUEST_RE`, sem número de domínio). O objetivo do Modelo (hipertrofia/força) não pede cardio sozinho. Sem pedido: prompt e schema da v12 **letra por letra** (snapshot dos 6 casos antigos com o mesmo `prompt_sha256`) e todo cardio da saída sai com `CARDIO_REMOVED`/`not_requested` |
| Contexto de recuperação | TSB mais recente do Intervals.icu pela leitura compartilhada `IntelligenceServiceBase.get_latest_intervals_wellness` + `wellness_freshness.is_row_stale` (sem SQL novo; velho demais ou ausente = faixa desconhecida). Só com cardio pedido (nenhuma leitura a mais sem cardio). `cardio_envelope.template_signals`: no Modelo vale só a faixa do TSB; queda de HRV, FC de repouso e perna sem liberação são sinais de curto prazo (`RECOVERY_SIGNAL_DAYS`) e não moldam um ciclo recorrente |
| Schema da saída | `output_schema.OUTPUT_SCHEMA_WITH_CARDIO`: `cardio_items` por slot (lista; `cardio_mode` com o enum de `cardio_prescription.CARDIO_MODE_VALUES` e os campos com os nomes das colunas, anuláveis; sem equipamento). Fora de `items` de propósito: no modo estrito todo campo é obrigatório e os campos de cardio em cada exercício custariam ~10 nulos por item na SAÍDA. Sem pedido, `OUTPUT_SCHEMA` (o da v12) |
| Prompt | `cardio_plan.prompt_section`: campos e unidades, `cardio_prescription.limits_text()`, `CARDIO_SESSION_MAX_MINUTES`, padrões por `default_prescription` (o `describe(DEFAULTS[HIIT])` sem o modo saía sem as séries; o prompt do agente v3 tinha esse texto, corrigido no PR do botão de cardio abaixo; guarda `cardio_padrao_sem_modo`), `WEEKLY_CARDIO_SESSIONS_MIN/MAX`, `preference_ladder_text`/`hiit_protocol_text` (`cardio_policy`), `LEG_MUSCLE_GROUPS`, `WEEKLY_HIIT_MAX`, `BAND_WEEKLY_MINUTES_MAX`/`BAND_MAX_ZONE` da faixa, `CONTINUOUS_SESSION_MAX_MINUTES`, `HIIT_MIN_TSB`/`TSB_HIGH_RISK_BELOW`, zonas por `hr_zones.zone_label`. Vai depois dos dados (o prefixo cacheável da v12 não muda) |
| RAG | `rag_chunking.select_periodization_cardio_rag_text`: `PERIODIZATION_CARDIO_CHUNK_IDS` (10 chunks de `CARDIO.md`, placeholders `{{cardio.*}}` interpolados), teto `MAX_RAG_PERIODIZATION_CARDIO_CHARS`; mede 2.713 caracteres (o recorte do agente, 5.978). Fora do orçamento `MAX_RAG_*` da ficha (entra só com cardio pedido) |
| Depois do LLM | `cardio_plan.apply_cardio`, etapa 9 do `validate_draft`, DEPOIS de toda a cascata de força (complemento de volume, ativação e metodologia continuam sem enxergar cardio, como na v12): `cardio_prescription.clamp_fields` + `validation_error` (inválido sai com `CARDIO_REMOVED`/`invalid_prescription`; campo ajustado ao limite, `CARDIO_ADJUSTED`/`clamped`) → `cardio_envelope.apply_template_envelope` (HIIT no dia de perna ou na véspera vira Normal; zona e tempo limitados; excedente do dia e tetos da semana cortados; cada ajuste com o código do dono e a mensagem com os rótulos de `cardio_recommendation_v3`) → item `item_kind='cardio'` no FIM do slot por `template_drafts.cardio_item_payload`. Slot de descanso com cardio continua descanso; slot removido pelo auto-fix estrutural leva o cardio junto (`slot_removed`) |
| Tempo de treino | `time_estimate` já soma o item de cardio por `cardio_prescription.total_seconds` (teste da C5 confere no slot gerado) |
| Projeção do ciclo | `cardio_envelope.project_template_slots`: todos os slots com `weekday` (sem repetir) = dia da semana (véspera de segunda = domingo); senão a sequência do ciclo (o último slot é a véspera do 1º). Tetos da semana pela segunda-feira de `cardio_adherence.week_start` sobre datas sintéticas a partir de `TEMPLATE_ANCHOR_MONDAY` (ciclo de 8 slots em sequência cruza para a semana seguinte) |
| Frontend | `lib/periodization.ts` só mostra `message` do backend para `CARDIO_ADJUSTED`/`CARDIO_REMOVED`; o builder já abre itens de cardio (C1). Modal da geração (PR do botão de cardio): seletor de três estados `PERIODIZATION_CARDIO_OPTIONS` (Automático/Incluir/Sem cardio); Automático (padrão) não envia `include_cardio`, Incluir envia `true`, Sem cardio envia `false`. Só rótulo e texto de tela: o "quando" do Automático é do dono `cardio_plan.cardio_requested` (o backend não expõe texto próprio do gatilho, então a descrição da opção é texto de tela, sem número) |
| Eval | 4 casos novos (`cardio_pedido_modo_b_4_dias`, `cardio_proibido_pelo_usuario`, `cardio_hiit_vespera_de_perna`, `cardio_no_dia_de_descanso`) e o critério `_avaliar_cardio_da_periodizacao` (dono da prescrição e envelope do Modelo na saída crua) |

**Decisões aprovadas pelo usuário em 27/10/2026:** (1) gatilho = flag do pedido ou menção a cardio nas preferências;
(2) a faixa do TSB do momento da geração vale para o Modelo inteiro (TSB baixo gera Modelo sem HIIT, com o motivo no
relatório; o usuário edita); (3) os tetos semanais do agente (sessões, HIIT, minutos) valem também para o Modelo.
Desde 27/10 o envelope do Modelo veta HIIT só na véspera do dia de perna e usa os cortes -25/-35 (§69.8;
`periodization-v14`).

**Impacto em produção:** só Modelos gerados depois do deploy e só quando o pedido tem cardio. Modelos já salvos,
rascunhos antigos e a geração sem cardio não mudam (schema e prompt da v12). Medição (produção, SELECT de
2026-10-27): 7 gerações em `template_generation_logs` desde 2026-09-20 (a última em 2026-09-28); aplicando
`CARDIO_REQUEST_RE` às `instructions` delas, **0 de 7** teriam disparado o cardio — nenhuma geração passada mudaria.
Custo com cardio pedido: ≈ +6,3 mil caracteres de entrada (seção ≈ 3,3 mil + RAG 2,7 mil + schema 0,9 mil;
≈ +1,6 mil tokens) e ≈ +0,3 a 0,5 mil tokens de saída ≈ +US$ 0,0035 por geração (gpt-5.4-mini); sem cardio, zero.

**Guardas:** nenhuma allowlist mudou.

**Botão de cardio + padrão do HIIT no agente v3 (2026-10-27, branch `feat/m98-botao-cardio-fix-hiit`).**
(1) O modal de geração do Modelo ganhou o seletor de cardio (linha "Frontend" acima): o pedido só leva
`include_cardio` quando o usuário escolhe Incluir ou Sem cardio. (2) O `SYSTEM_PROMPT` do `cardio-agent-v3` montava os
padrões com `describe(DEFAULTS[modo])`; sem o `cardio_mode` o dono lê o item como Normal e o HIIT saía
"5 min aquec. + 5 min recuperação". Agora usa `cardio_prescription.default_prescription(modo)` (o mesmo da C5): o HIIT
sai com as séries (`N× [s Zx / s Zy]`). Nenhum outro consumidor chamava `describe`/`summarize`/`total_seconds` sobre
`DEFAULTS[...]` (`cardio_envelope._to_continuous` e `rag_placeholders` leem campo a campo, o que é correto). Guarda
nova `cardio_padrao_sem_modo` (proibição pura, allowlist vazia): chamada de leitura do dono sobre `DEFAULTS[...]` direto.
**Versão do prompt:** fica `cardio-agent-v3`. A v3 nunca chegou à produção (`main` está na `cardio-agent-v2`), e o eval
pago v2 × v3 foi rodado já com a correção, então a v3 que sobe é a corrigida; só os `llm.usage` do DEV de 26–27/10
misturam as duas formas (sem efeito em custo ou cache: a `prompt_cache_key` casa por prefixo).
**Impacto em produção:** nenhum hoje. A v3 e o modal chegam juntos na próxima promoção; sem escolha no modal, o pedido é
o de antes (byte a byte). Snapshot: só os 6 `.txt` do `cardio_agent` mudam (1 linha cada).


### 69.8 M98 — decisões do usuário de 27/10/2026: aprovações e três ajustes

Branch `feat/m98-ajustes-decisoes`. Prompts: **`cardio-agent-v4`** e **`periodization-v14`** (convenção de
`docs/ia/EVALS.md` §3: o texto do prompt mudou, a versão sobe).

**Aprovados pelo usuário em 27/10/2026 (deixam de ser provisórios; nenhum número mudou além dos ajustes abaixo):**

- ≤ 2 HIIT por semana (`cardio_envelope.WEEKLY_HIIT_MAX`); sessão intensa = ≥ 300 s em Z4+ (`HIGH_INTENSITY_*`);
- veto de HIIT com HRV −15% (`HRV_DROP_BAD_PCT`) ou FC de repouso +5 bpm (`RESTING_HR_RISE_BAD_BPM`), por 2 dias
  (`RECOVERY_SIGNAL_DAYS`);
- padrões Normal e HIIT (`cardio_prescription.DEFAULTS`, `DEFAULT_*`);
- tetos de prescrição e do agente: parte principal do Normal ≤ 60 min (`CONTINUOUS_SESSION_MAX_MINUTES`), 3–6
  sessões por semana (`WEEKLY_CARDIO_SESSIONS_MIN/MAX`), 1 por dia (`SESSIONS_PER_DAY_MAX`), passo de 5 min
  (`cardio_policy.CONTINUOUS_PROGRESSION_STEP_MINUTES`), `FIELD_LIMITS` e `CARDIO_SESSION_MAX_MINUTES`;
- aderência: 90%/70% (`ADHERENT_TIME_MIN_PCT`/`ADHERENT_ZONE_MIN_PCT`), 50% (`PARTIAL_TIME_MIN_PCT`), janela de 60 min
  do casamento (`intervals_hr_match.DEFAULT_TOLERANCE_SECONDS`);
- cores das zonas no dashboard e a saída do item "Cardio" do menu principal (§69.6);
- gatilho do cardio no Modelo e faixa do TSB da geração valendo para o Modelo inteiro (§69.7);
- regras do "Aplicar no Modelo" (§69.5): só admin/PT, recusa em conflito, datas passadas, versão vigente, PT com
  vários alunos;
- % da FCmáx 50/60/70/80/90/100 (`hr_zones.MAX_HR_ZONE_BOUNDS_PERCENT`); mapeamento 7 → 5 (`INTERVALS_ZONE_MAP`);
  ordem do esporte de referência (`INTERVALS_REFERENCE_SPORT_TYPES`); FCmáx manual de 100 a 230 bpm
  (`MANUAL_MAX_HR_MIN_BPM`/`MANUAL_MAX_HR_MAX_BPM`).

**Três mudanças de comportamento (decididas em 27/10/2026):**

| # | Mudança | Dono | Consumidores conferidos |
|---|---|---|---|
| 1 | Cortes do TSB: `HIIT_MIN_TSB` -20 → **-25**, `TSB_HIGH_RISK_BELOW` -30 → **-35**. Zonas máximas (Z5/Z3/Z2) e minutos da semana (240/150/60) não mudam | `domain/cardio_envelope.py` | agente (`cardio_agent_ai`: prompt interpola os dois cortes; `build_envelope`) e Modelo (`cardio_plan._band_line`/`template_signals` → `build_template_envelope`) |
| 2 | HIIT vetado **só na véspera** do dia de perna; no próprio dia de perna é permitido, depois da força. `REASON_LEG_DAY` continua código estável (recomendações gravadas o citam), mas não entra mais em `reasons`; o dia de perna sai em `DayEnvelope.leg_day`. "Perna" não muda (`LEG_MUSCLE_GROUPS`, Panturrilha fora) | `domain/cardio_envelope.build_envelope` | agente (`apply_envelope`), Modelo (`apply_template_envelope`), prompts (`cardio_agent_ai.SYSTEM_PROMPT`, `cardio_plan.prompt_section`), eval (`_avaliar_cardio_da_periodizacao` usa o envelope, sem regra própria) |
| 3 | Aderência do cardio em dia de DESCANSO: o slot de um dia passado vem da **ficha salva para a data** (a mais recente da data); sem ficha, o dia não é avaliado. Ficha de slot `descanso` com cardio, sem sessão de força no dia, casa pelo dia local inteiro (`intervals_hr_match.match_local_day`; várias = a mais longa, `MATCHED_AMBIGUOUS`); a atividade já casada com a força de um dia vizinho não entra; o dia de hoje só conta depois de casado (`cardio_adherence.rest_day_due`), dia futuro nunca | `domain/cardio_adherence.py` (`ANCHOR_LOCAL_DAY`, `rest_day_due`) + `intervals_hr_match.match_local_day` (casamento) | `cardio_adherence_service.build_adherence` (SQL novo `REST_DAY_PLANS_WITH_CARDIO_SQL`, +1 statement fixo), `GET /integrations/intervals/cardio-adherence` (passa o hoje do usuário), dashboard (`cardio_service.load_adherence`/`adherence_view`: `anchor` e `workout_plan_id` por sessão; métrica `cardio-adherence-v2`), contexto do agente (`cardio_context_v3._adherence`). Nenhum terceiro critério de casamento |

**Datas:** `workout_plans.plan_date`, `activities.local_date` e `intervals_activities.start_time`
(`start_date_local` do Intervals) já são do dia LOCAL; o hoje vem de `time_windows.user_today` (rota) ou do fim da
janela (dashboard/contexto, que terminam hoje).

**Impacto em produção (medido em 27/10/2026, SELECT só leitura no Aurora, usuário 1, 29/08–27/10, 60 dias):**

- Faixas de TSB (`intervals_wellness`, TSB de -26,5 a -8,9): antes `ok` 55 / `loaded` 5; depois `ok` 58 / `loaded` 2;
  **3 dias mudam de faixa** (`loaded` → `ok`); nenhum dia em `high_risk` nos dois cortes.
- Perna: 45 dias com força, **16 dias de perna**; nenhum seguido de outro dia de perna, então **os 16 passam a
  permitir HIIT** pela regra de perna (15 com a faixa nova do TSB; 13 seriam com a antiga). Somando perna + TSB, os
  dias com HIIT permitido sobem de 27 para 43 de 60 (os outros vetos — teto de 2 HIIT/semana e HRV/FC de repouso —
  continuam valendo).
- Aderência em dia de descanso: **0 fichas de descanso** salvas em produção (todos os usuários; 45 fichas do usuário
  1 e 4 do usuário 5, todas de slot `normal`), embora os Modelos tenham 62 slots `descanso` (15 com cardio, somando
  as versões). Nenhuma sessão nova de aderência hoje; distribuição de status: vazia. A regra passa a valer quando
  uma ficha de descanso for salva.
- Prompts: `cardio-agent-v4` muda 2 linhas do prompt (regra de perna e cortes do TSB) e o envelope do contexto
  (sem `leg_day` em `reasons`); `periodization-v14` muda 2 linhas da seção CARDIO NO MODELO; sem pedido de cardio, o
  prompt da periodização é o mesmo da v13/v12 (`prompt_sha256` igual nos 6 casos sem cardio; só a
  `prompt_cache_key` muda com a versão). Eval pago v3 × v4 e v13 × v14: não rodado neste PR.

**Guarda:** nenhuma allowlist mudou (`envelope_cardio_paralelo` e `aderencia_cardio_paralela` seguem vazias).

## 70. M99 — EPOC e PAI de 7 dias ESTIMADOS do tempo em zona de FC (2026-10-27)

Branch `feat/m99-esforco-estimado`. A seção "Esforço (EPOC / PAI)" de Indicadores físicos deixa de ser lançamento
manual e passa a mostrar SÓ o valor estimado. EPOC (Firstbeat) e PAI (Zepp) são proprietários: o app estima.

**Dono:** `api/app/domain/cardio_load.py` (Python puro, versão `cardio_load_v1_7d`).

- Fórmula: para cada dia D, soma das atividades do Intervals de D-6 a D (inclusive) de
  `minutos na zona z × peso_z`, nas **7 zonas do Intervals** (Z1–Z4, Z5a, Z5b, Z5c; `icu_hr_zone_times`, gravado em
  `intervals_activities.hr_zone_seconds` pela migração 097). Arredonda para inteiro só no fim. Janela sem nenhuma
  atividade com tempo em zona = `None`. Atividade com outra quantidade de zonas não entra.
- Pesos por minuto (Z1..Z5c), não negativos e não decrescentes:
  EPOC `(0.26, 0.90, 0.90, 3.92, 3.92, 3.92, 16.19)`, PAI `(0.02, 0.64, 0.90, 1.11, 1.11, 1.11, 6.03)`.
- Janela de leitura: `activity_read_start(primeiro_dia)` = primeiro dia − 6; quem lê não faz a conta.

**Origem dos pesos (calibração, `tools/calibrar_cardio_load.py`, só leitura):** 38 dias anotados do relógio em
`manual_effort_metrics` (usuário 1, 24/07–30/08/2026) contra o tempo em zona das atividades do Intervals; mínimos
quadrados não negativos com monotonia (w_z = d_1 + … + d_z, d ≥ 0) e leave-one-out.

| Janela | EPOC MAPE (LOO) | EPOC MAE | PAI MAPE (LOO) | PAI MAE |
|---|---|---|---|---|
| 1 dia (premissa inicial "valor do dia") | 23,9% | 59,8 | 23,9% | 17,1 |
| 3 dias | 16,8% | 42,3 | 12,4% | 9,0 |
| 5 dias | 12,3% | 31,4 | 10,3% | 7,2 |
| **7 dias (adotada)** | **11,5%** | **29,5** | **6,4%** | **4,6** |
| 10 dias | 11,7% | 30,0 | 9,2% | 6,5 |
| 14 dias | 10,5% | 26,0 | 10,6% | 7,4 |
| referência: média constante | 16,3% | — | 16,8% | — |

A premissa inicial (valores manuais = valor do dia) errava mais que repetir a média; o anotado é o acumulado de
7 dias que o relógio mostra. **Decisão revista pelo usuário em 27/10/2026:** acumulado de 7 dias. Pesos do motor nos
38 dias (in-sample): MAPE 10,6% (EPOC) e 5,6% (PAI).

**Consumidores (só consomem o valor pronto):**

| Consumidor | O que lê |
|---|---|
| `GET /effort-metrics?days=N` (`effort_metrics.py`) → `EffortMetricsSection.tsx` | `[{local_date, epoc_7d_estimated, pai_7d_estimated, model_version}]`, dias com valor, do mais recente; só leitura |
| `cardio_context_service._get_effort_estimates` (contexto v1, `/analytics/cardio-agent-context`, relatório) | `effort_metrics_estimated` (substitui `manual_effort_metrics`) |
| `cardio_context_v3._effort_estimates` (agente `cardio-agent-v5`) | `effort_metrics_estimated` sem a versão por linha; o prompt descreve "EPOC 7 dias e PAI 7 dias ESTIMADOS" |
| `dashboard/cardio_service.load_pai` (`/dashboard/cardio`) | `pai.points` com o PAI de 7 dias estimado, `estimated`, `model_version`; a meta segue `cardio_policy.PAI_TARGET_MIN` (semanal) |
| `agente-cardio/page.tsx`, `AgenteCardioCharts.tsx`, `DashboardCardioClient.tsx` | rótulos "EPOC 7 dias (estimado)" e "PAI 7 dias (estimado)"; nenhum número no TS |

**Saiu:** o CRUD `/manual-effort-metrics` (`manual_effort_metrics.py`) e o formulário. A tabela
`manual_effort_metrics` fica (sem migração destrutiva): histórico e base da calibração.

**Guarda:** `pesos_esforco_paralelos` (allowlist vazia).

**Limite da calibração:** um único usuário (usuário 1) e um único período (24/07–30/08/2026). Não há validação
fora dessa amostra; uma recalibração (nova versão do modelo) entra pelo mesmo `tools/calibrar_cardio_load.py`.

**Impacto em produção (medido em 27/10/2026, SELECT só leitura no Aurora, usuário 1):**

- Cobertura do tempo em zona (`intervals_activities.hr_zone_seconds`, backfill da 097): 540 de 544 atividades
  do usuário têm a coluna; no período da calibração 173 de 175; nos últimos 60 dias (com a janela de leitura)
  217 de 217. A subestimação por atividade sem zona é desprezível hoje.
- Últimos 60 dias (29/08–27/10): **60 de 60 dias** passam a ter EPOC e PAI de 7 dias estimados (antes: nenhum
  registro manual no período; o último é de 30/08). Últimos 7 dias: PAI 68–91, EPOC 265–310.
- Estimado × relógio nos 38 dias anotados: MAPE 10,6% (EPOC) e 5,6% (PAI) com os pesos do motor (tabela dia a dia
  na descrição do PR do M99).
- Semântica: o PAI do agente (`cardio-agent-v5`), do relatório e do `/dashboard/cardio` deixa de ser o registro
  manual (vazio desde 30/08) e passa a ser o acumulado de 7 dias estimado; a meta (`PAI_TARGET_MIN`) não muda.
  O histórico manual deixa de aparecer na tela (decisão do usuário: a tela mostra só o estimado); os dados
  ficam intactos na tabela.

## 71. M102 — reps e RPE por lado em exercício unilateral (2026-10-27)

**Decisão do usuário (27/10):** uma linha por série, com colunas por lado; reps e RPE oficiais = **pior lado**
(menos repetições, maior RPE). A carga continua única e TOTAL (`weight_kg`, D12).

**Dono:** `api/app/domain/set_sides.py` (Python puro, constantes nomeadas).

- `canonical_reps(reps_left, reps_right)` = menor lado; `canonical_rpe(rpe_left, rpe_right)` = maior lado; lado
  vazio ignorado; nenhum lado → o valor enviado vale (`canonical_from_sides`). Reps e RPE são independentes.
- `apply_canonical(dict)` / `apply_canonical_to_model(modelo)`: o recálculo, chamado por todo caminho que grava.
- `sides_text(row)` → "reps E 10 · D 8; RPE E 8 · D 9" (telas, prompts); `WORST_SIDE_RULE_TEXT` (frase da regra,
  interpolada no relatório Markdown e nos prompts); `PROMPT_VERSION_SUFFIX = "+sides-v1"` (ramo novo dos
  prompts, só quando há lados).

**Schema:** migração `100_series_por_lado.py` — `exercise_sets.reps_left/reps_right INT NULL`,
`rpe_left/rpe_right DECIMAL(4,2) NULL`, INSTANT, sem FK, sem índice, sem backfill; chave única intacta.

**Gravação (o backend sempre recalcula):**

| Caminho | Onde |
|---|---|
| `POST /workout-sessions/{id}/exercises/{id}/sets` | `SessionSetIn._apply_sides` (antes das validações de medida e do Modo Intensivo) |
| `PATCH .../sets/{n}` | `merge_set_patch` (lados em `PATCHABLE_SET_FIELDS`) → `apply_canonical` |
| `PUT /exercises/logs/{id}` | `merge_sets_for_update` (lados em `PRESERVED_SET_FIELDS`: omitido preserva) → `apply_canonical` |
| `POST /activities` / `upsert_activity` | `ExerciseSetIn._apply_sides` + `apply_canonical` por série antes dos agregados |

A resposta do POST/PATCH devolve `reps`/`rpe` oficiais, os lados e `sides_text`; o Modo Treino usa esses
valores para a avaliação por IA (nunca recalcula). O relógio e a fila offline não mandam lados: nada muda.

**Motores:** nenhuma alteração. Todos leem `reps`/`rpe` (pior lado). Semântica aceita: a **tonelagem** usa as
reps do lado fraco × a carga total; a progressão segue o lado fraco.

**Gatilho na tela:** `exercise_catalog.is_unilateral` (devolvido em `GET /workout-plans/{id}` como
`is_unilateral` do item). Só a série de trabalho/backoff do card padrão do Modo Treino mostra E/D; técnicas
avançadas, fluxo avançado, Modo Intensivo, treino livre e o relógio ficam fora da v1 (valor único). Sem backfill
nem curadoria automática: a lista de candidatos para o usuário marcar está na descrição do PR.

**Saídas:** histórico/edição (`WorkoutDetailClient`, `exercise-set-edit.ts` reenvia os lados),
`GET /exercises/last-session-sets` (`sides_text`), resumo de exercício e de treino e avaliação por série
(`sides_text` + `WORST_SIDE_RULE_TEXT`, versão `+sides-v1`), relatório Markdown (linha "Unilateral" da
metodologia, `WORST_SIDE_RULE_TEXT`).

**Guarda:** `pior_lado` (allowlist vazia).

**Impacto em produção:** nulo até a marcação. As colunas novas nascem NULL, nenhuma série existente muda e o
canônico (`reps`/`rpe`) é idêntico ao atual; o efeito (tonelagem e progressão pelo lado fraco) só aparece
quando o usuário marcar `exercise_catalog.is_unilateral` e lançar lados. Prova e candidatos:
`docs/motores/m102_candidatos_unilaterais.sql`.

**Backlog:** M102-B1 (insight de assimetria entre lados), M102-R (lados no relógio).

## 72. M100 — Exercício laboral (2026-10-27)

Branch `feat/m100-exercicio-laboral`, migração `099_exercicio_laboral.py`. Exercício de aquecimento/alongamento
(ex.: manguito rotador) sem progressão: no Modo Treino só mostra séries, reps e carga e oferece "Concluir" ou "Pular".

**Decisões do usuário (27/10/2026):** o laboral conta **só no tempo de treino** (fora de progressão, recorde/e1RM,
volume, ativação, recuperação e insights); séries, reps e carga vêm de um **padrão no catálogo**.

**Donos:**

| Regra | Dono | Consumidores (só consomem) |
|---|---|---|
| Série laboral é PREPARATÓRIA (`set_type='laboral'`) | `api/app/set_types.py` (`LABORAL` em `PREPARATORY_SET_TYPES`, `is_laboral_set`); espelho FE `set-types.ts` (`LABORAL_SET_TYPE`, `isLaboralSetType`) via `set-types.generated.json` | todo agregado já filtra por `sql_not_preparatory()`/`is_working_set()`/`non_preparatory_sets()`: volume (`VolumeService`, `compute_exercise_stats`), recorde/e1RM, progressão, recuperação (`recovery_read_model._ES_NOT_PREPARATORY`), fadiga, ativação realizada, relatórios, insights |
| Padrão laboral (séries/reps/carga): catálogo > linha da ficha > fallback (`LABORAL_FALLBACK_SETS`/`_REPS`); limites do admin (`LABORAL_MAX_*`); colunas do SELECT (`sql_catalog_columns`); campos da linha (`laboral_row_fields`) | `api/app/domain/laboral.py` (Python puro); leitura em `api/app/laboral_catalog.py` | congelamento (`services._laboral_rows_snapshots`, `engine_version=laboral-v1`, grava `sets_count`/`reps_target`/`load_target` e `prescription_json.laboral`); rascunho do Modo B (`mode_b`); admin (`exercise_catalog_admin.clean_laboral_fields`); leitura da ficha (`get_workout_plan` → `is_laboral` + `laboral {sets, reps, load_kg}`) |
| Laboral fora da prescrição do motor | `generation/deterministic_prescription.prescribe_pairs` (via `progression_state.load_laboral_ids`): o par laboral não entra no resultado nem no contexto de fadiga planejada | Modo B (rascunho e congelamento), Modo A, `prescription_service.suggest_for_pairs` (linhas manuais) |
| Laboral fora da ativação planejada | `intelligence/technique_volume.planned_item_sets` (item com `is_laboral` = 0 séries; `ACTIVATION_PAYLOAD_FIELDS` leva a flag) | prévia/breakdown da ficha (`MuscleActivationExerciseIn.is_laboral`, `activation-payload.ts`), resumo do Modo B |
| Laboral nunca é candidato de grupo | predicado `domain/laboral.sql_not_laboral(alias)`, usado em `equipment_recommender._load_candidates` e `exercise_substitution.load_group_candidates` | Modo A, recomendação por grupo, substituição M6 |
| Série de exercício laboral é sempre `laboral` | `workout_sessions.add_session_set` força o `set_type` quando o exercício (pelo `mapping_id`) é laboral (`laboral_catalog.load_laboral_ids`) | relógio e clientes antigos que mandam `working` |

- **Tempo de treino:** `periodization/time_estimate.py` (e o espelho) conta o item laboral como qualquer item, pelas
  séries/reps da linha (que o congelamento já gravou com o padrão).
- **Modo Treino:** `LaboralExerciseCard` só leitura ("3 × 15 · 2 kg" de `planExercise.laboral`, pronto do backend);
  "Concluir" grava as N séries com `set_type='laboral'` pelo POST de `/sets`; "Pular" tira da fila (cancelado) sem
  gravar nada. Sem RPE, descanso, avaliação por série nem resumo de IA; `workout_sessions._evaluate_set` não calcula
  carga da próxima série para série laboral. O fluxo da tela (`lib/workout-mode-laboral.ts`) não tem número.
- **Guarda:** `padrao_laboral` (allowlist vazia): coluna `laboral_default_*` citada, ou `is_laboral = 0/1` escrito
  à mão, em `api/app` fora do dono.

**Fica para depois (backlog):**

- **M100-B1** — prévia de volume do EDITOR do Modelo (`workout_plan_templates`, itens Pydantic sem a flag) ainda
  conta o item laboral como séries planejadas; a ficha gerada, o rascunho e a prévia da ficha já não contam.
- **M100-R** — relógio: o `workout-mode-core` recebe a flag pela ficha, mas o app do relógio ainda não tem o card
  laboral. Uma série gravada pelo relógio num exercício laboral já sai `laboral` (o servidor força o tipo); falta a
  tela. Exercício gravado com `mapping_id` legado (id absorvido) não é reconhecido pelo forçamento.

**Impacto em produção:** nenhum dado muda. `is_laboral` nasce 0 para todo o catálogo e os padrões NULL; nada vira
laboral até o admin marcar. O histórico anterior à marcação continua `working` (não é reescrito): ao marcar o
manguito rotador, as séries antigas dele seguem contando em volume/recorde/progressão; só as novas (`laboral`) saem.
Medição em 27/10/2026 (SELECT só leitura no Aurora de produção): candidatos ao laboral = 94 "Rotação externa do
ombro na polia" (27 séries `working`, 11 sessões, 18/08–26/10) e 95 "Rotação interna do ombro na polia" (16 séries,
6 sessões, 16/08–26/10), ambos de 1 usuário; 0 séries `laboral`; 122 exercícios no catálogo;
`exercise_catalog.TOTAL_ROW_VERSIONS` = 6 (limite 64). O gatilho de mudança é a MARCAÇÃO pelo admin: a partir dela, o
congelamento (criar/atualizar ficha) grava o padrão nas linhas da ficha desse exercício; os itens do Modelo não mudam.
Reversão: desmarcar no admin e atualizar a prescrição da ficha.

## 73. RAG no submódulo privado `api/privado` — M103 P0 (2026-10-29)

O projeto passa a ter um espelho público (`muscle_fit_v2`). O RAG e, nos PRs seguintes, os prompts de sistema
moram no repositório **privado** `muscle_fit_privado`, montado como submódulo em `api/privado/` (URL relativa
`../muscle_fit_privado.git`). Não muda dono nenhum nem regra nenhuma: só o caminho do texto injetado em prompt.

| Antes | Agora |
|---|---|
| `rag/markdown/**/*.md` | `api/privado/rag/markdown/**/*.md` (mesmo conteúdo, mesma árvore git) |
| `prompts` no código (`api/app/**`) | continuam lá; `api/privado/prompts/` nasce só com o README do formato `{{placeholder}}` |

- **Princípio 4 inalterado**: o Markdown do submódulo continua sendo prompt, com número só por placeholder
  (`app/rag_placeholders.py`).
- **Guarda**: `arquivos_varridos` varre `api/privado/rag/markdown` (`.md`); os escopos que citavam
  `rag/markdown/...` (`faixa_reps_prosa`, `numeros_rag_cardio`, o escopo de nutrição) passam a citar o caminho novo.
  Allowlist inalterada (nenhuma entrada era de arquivo do RAG). `test_guarda_varre_o_rag_do_submodulo_privado`
  falha se a varredura não achar `.md` (sem o submódulo ela pularia o RAG em silêncio).
- **Produção**: o S3 do RAG é publicado por `infra/s3_rag.tf` a partir do submódulo, com as mesmas keys
  `markdown/<caminho>`; as Lambdas continuam lendo do S3. Uma `precondition` no bucket faz o plan falhar com o
  fileset vazio, e `terraform.yml`/`deploy-producao.yml` conferem o diretório antes do plan.

**Impacto em produção:** nenhum. Mesma árvore git (`rag/markdown` = `26afbfa3`) nos dois repositórios, então os
bytes e os etags (`filemd5`) são os mesmos e o plan do `aws_s3_object.musclefit_rag_markdown` não muda objeto
nenhum; imagens Docker sem o RAG (como antes); nenhuma Lambda muda.

### 73.1 Prompts de 4 features no submódulo privado — M103 P1 (2026-10-29)

A **prosa** dos prompts de sistema de quatro features sai dos literais `.py` para templates do repositório privado,
`api/privado/prompts/<feature>/<PROMPT_VERSION>.md` (fragmento de um ramo do builder: `<versão>.<parte>.md`),
lidos por `app/prompt_store.py`. Não muda dono nem regra: o builder público continua decidindo a ordem, os ramos, os
dados (JSON) e a `PROMPT_VERSION`, e cada número continua vindo do dono pela tabela de valores da feature.

| Feature (consumidor) | Template(s) | Tabela de valores (código público) |
|---|---|---|
| `nutrition_label_ai.py` | `nutrition_label/nutrition-label-v2.md` | `prompt_values()` + a lista de campos (`labels`, `label_photo`, `catalog`, `meal_math`) |
| `cardio_agent_ai.py` | `cardio_agent/cardio-agent-v5.md` | `prompt_values()` (`cardio_policy`, `cardio_prescription`, `cardio_envelope`, `hr_zones`, `prompt_user_data`); `system_prompt()` renderiza sob demanda (era f-string avaliado no import) |
| `exercise_description_ai.py` | `exercise_description/exec-desc-v1.md` + `.equipamentos`, `.sem_equipamentos`, `.musculos`, `.sem_musculos` | `build_prompt` (`MAX_SETUP_STEPS`, `MAX_EXECUTION_STEPS`, `MAX_CUES`, dados do catálogo) |
| `nutrition_meal_parse_ai.py` | `nutrition_meal_parse/nutrition-parse-v1.md` + `.pedido` | `prompt_values()` (`meal_math`, `meal_parse`, `labels`, `prompt_user_data`) |

- **`prompt_store`**: `load(feature, nome)` (cache por processo), `render(feature, nome, valores)` com
  `rag_placeholders.interpolate(strict=True)`. Falha alto (`PromptTemplateError`): template ausente, placeholder sem
  valor, valor sem placeholder ou valor não textual. Diferente do RAG, que em produção deixa o placeholder e só loga.
  Caminho: `MUSCLEFIT_PROMPTS_DIR` ou `api/privado/prompts` relativo ao pacote; nas imagens (`api/Dockerfile*`) entra
  SÓ `privado/prompts` (o RAG e o resto do submódulo continuam fora; o CI confere).
- **Guarda**: varre `api/privado/prompts/**/*.md` como o literal do módulo consumidor (§5). Allowlist inalterada
  (nenhum template tem ocorrência; nenhuma entrada era dos 4 módulos).
- **Snapshots com texto de prompt** (todas as features) vão para `api/privado/tests/fixtures/`; o público guarda só
  `llm_prompts/index.json` (hashes) e `nutrition_prompts/label_photo.body.json` (corpo sem o texto). A comparação
  nunca imprime o texto no log público (`tests/support/privado.py`).
- **Testes com frase de prompt** das 4 features vão para `api/privado/tests/unit/` (rodam no `backend-unit` do CI
  público; a falha sai sem traceback, `api/privado/tests/conftest.py`).
- Fica no público, por ora: `prompt_user_data.USER_DATA_RULE` (compartilhada com as outras features, entra como
  valor `regra_dados_usuario`; saiu no P2, §73.2) e os rótulos curtos de dado do catálogo na descrição de execução (nomes dos atributos,
  "primarios"/"secundarios"). Saem com as próximas features (P2+).

**Impacto em produção:** nenhum (bytes idênticos). `prompt_sha256` e `body_sha256` de todos os casos de
`cardio_agent`, `exercise_description` e `meal_parse` em `index.json`, e o sha256 do prompt do rótulo em
`label_photo.body.json`, iguais aos da `develop`; os snapshots passam sem `LLM_PROMPT_SNAPSHOT_WRITE`;
nenhuma `PROMPT_VERSION` muda.

### 73.2 Prompts de insights, análise por IA, resumos e fragmentos compartilhados — M103 P2 (2026-10-29)

Mesmo padrão do §73.1 para mais quatro features e para os dois fragmentos que elas (e as outras) compartilham.
Nenhum dono nem regra muda: o builder público decide ordem, ramos, dados e versão; o número vem do dono pela tabela
de valores; a prosa dos motores (`format_decision`, `describe_ruleset`, `reduced_dose_prompt_rule`, `limits_text`,
`set_sides.WORST_SIDE_RULE_TEXT`...) continua pública, porque é texto de regra do motor.

| Consumidor | Template(s) (`api/privado/prompts/`) | Valores (código público) |
|---|---|---|
| `insights_prompt.py` | `insights/insights-prompt-v6.md` (papel, regras 1–7, formato, rótulos da janela) | `build_insight_prompt`: foco (`goal_prompt_context.focus_text`), blocos de objetivo/deload, RAG filtrado, janela, qualidade, `effective_prompt_version`, pacote JSON, pergunta delimitada |
| `report_ai.py` | `workouts_analysis/workouts-analysis-v5.md` + `.rag` (as antigas `_COMMON_RULES`, `_PROGRESSION_RULES`, `_RECENT_RECORDS_RULE`, `_rag_section`) | `prompt_values()`: `PLATEAU_MIN_EXPOSURES` (`metrics_registry`), `E1RM_MAX_RELIABLE_REPS` (`domain/e1rm`), `RECENT_RECORDS_DAYS` (`progression_service`); `build_prompt` renderiza sob demanda (o f-string `_PROGRESSION_RULES` era avaliado no import) |
| `exercise_summary_ai.py` | `exercise_summary/exercise-summary-v3.md` + `.alvo`, `.sem_alvo`, `.dor`, `.anotacao`, `.motor`, `.sem_motor` | `build_prompt` (linhas das séries de `_format_sets`, decisão do motor por `format_decision`) |
| `workout_summary_ai.py` | `workout_summary/workout-summary-v5.md` | `build_prompt` (lista de `_format_exercises`, decisões por grupo, `format_tonnage`). O prompt renderizado entra em `input_fingerprint` (dedupe, `activities.ai_summary_input_sha256`): a chave não muda (teste `test_m103_p2_prompts_privados.py`, valores calculados com o código de antes) |
| `goal_prompt_context.py` | `goal_context/goal-v1.md` + `.regra`, `.teto`, `.tecnicas`, `.tecnicas_outras`, `.tecnicas_genericas`; `goal_context/deload-v2.md` + `.janela`, `.regra`, `.prescricao`, `.tecnicas`, `.sem_tecnicas` | `defaults_for`, `goal_target_rpe`, `goal_rpe_ceiling`, `goal_disallowed_techniques`, `deload_reason_label`, `DELOAD_SETS_REDUCTION_FRACTION`, `DELOAD_ALLOWED_TECHNIQUES`; `focus_text` e `goal_label_suffix` ficam públicos (só rótulos do dono `training_goal`) |
| `prompt_user_data.py` | `prompt_user_data/user-data-v1.md` | `user_data_rule()` (substitui a constante `USER_DATA_RULE`; os delimitadores `USER_DATA_OPEN`/`USER_DATA_CLOSE` entram como valor). Renderizada sob demanda por todos os prompts que a usam (inclusive avaliação de série, seleção legada v2, cardio e leitura da refeição) |

- **Guarda**: as seis pastas entram em `CONSUMIDOR_DO_PROMPT`. Allowlist inalterada.
- **Testes com frase de prompt** dessas features vão para `api/privado/tests/unit/` (`test_insights_prompt.py`,
  `test_workouts_analysis_prompt.py`, `test_exercise_summary_prompt.py`, `test_workout_summary_prompt.py`,
  `test_goal_context_prompt.py`); no público ficam versões estruturais e `test_m103_p2_prompts_privados.py`
  (template da versão existe, chave de dedupe igual, nenhuma linha de template em `api/app`).
- **Imagens**: o passo 2b do smoke (`.github/scripts/smoke-lambda-image.sh`) renderiza também insights, análise,
  os dois resumos e os blocos de objetivo/deload dentro de cada imagem.

**Impacto em produção:** nenhum (bytes idênticos). `prompt_sha256` e `body_sha256` de todos os casos de `insights`,
`workouts_analysis`, `exercise_summary` e `workout_summary` (e dos demais) em `index.json` iguais aos da `develop`;
os snapshots (inclusive `llm_prompts_objetivo`) passam sem `LLM_PROMPT_SNAPSHOT_WRITE`; nenhuma `PROMPT_VERSION`
muda; 1.461 combinações de ramos (objetivo × deload × técnica × alvo × decisão × dor, RAG vazio/cheio) comparadas
antes/depois, todas iguais.

### 73.3 Prompts da Periodização e da geração de fichas legada — M103 P3 (2026-10-29)

Mesmo padrão do §73.1 para a geração de Modelo por IA (Periodização, com a seção de cardio) e para os dois
prompts do caminho legado de geração de ficha (`workout_plan_templates_engine` v1 e seleção v2, ainda com
chamador atrás das flags; apagar continua no E14 parte 2). Nenhum dono nem regra muda: o builder público decide
a ordem das seções, os ramos e os dados; todo número vem do dono pela tabela de valores do builder.

| Consumidor | Template(s) (`api/privado/prompts/`) | Valores (código público) |
|---|---|---|
| `periodization/context.py` | `periodization/periodization-v14.*`: `.cabecalho`, `.regras`, `.modo_a`, `.modo_b`, `.contagem_primario`/`.contagem_ponderada` e `.checklist_contagem_*` (por `VolumeCountingMode`), `.checklist`, `.dados_usuario`, os títulos das seções (`.rag`, `.grupos`, `.tecnicas`, `.tabela`, `.sem_faixas`, `.catalogo`, `.metas_perfil`, `.metas_derivadas`, `.sem_metas`), objetivo por dia (`.objetivo`, `.objetivo_dia`, `.objetivo_regras`, `.objetivo_modo_a`, `.objetivo_compostos`, `.objetivo_isoladores`, `.objetivo_tecnicas`, `.objetivo_descanso`, `.objetivo_minimo`) e os exemplos (`.exemplo_celula`, `.exemplo_celula_exercicios`, `.exemplo_tecnica`). Versão = `core.PROMPT_VERSION` (`prompt_template_version()`) | `limits` (`SESSION_EXERCISES_*`, `MAX_PRIORITIZED_PER_SLOT`, `heavy_compound_range`, `session_exercises_min`, `day_item_goal`), `methodology_volume` (`_SETS_PER_EXERCISE_*`, `COUNTED_ROLES_BY_MODE`, `SECONDARY_SESSION_MIN_SETS`), `template_volume_domain` (cobertura do Modo A), `prescription_defaults` (`DEFAULT_REPS/RPE_TARGET`, `defaults_for`), `training_safety.goal_disallowed_techniques`, `technique_accounting.planned_equivalent`, a TABELA da metodologia do contexto |
| `periodization/cardio_plan.py` | `periodization_cardio/periodization-v14.md` + `.rag`, `.origem_flag`, `.origem_preferencias`, `.banda_sem_hiit`, `.banda_com_hiit`, `.tsb_sem_dado` | `cardio_envelope` (sessões/HIIT/minutos da semana, zona máxima e limites do TSB por faixa, grupos de perna), `cardio_prescription` (`limits_text`, `CARDIO_SESSION_MAX_MINUTES`, `describe(default_prescription)`), `cardio_policy` (escada de preferência, protocolo do HIIT, `format_decimal_br`), `hr_zones` |
| `workout_plan_templates_engine.py` | `plan_templates/plan-templates-v1.md` + `.dose_reduzida`, `.schema` | `MuscleRecoveryService.reduced_dose_prompt_rule()`, faixa de descanso do pedido, grupos em dose reduzida |
| `intelligence/template_ai_selector_v2.py` | `template_selector/template-selector-v2.md` + `.schema` | `user_data_rule()`, faixa de descanso do pedido, `DEFAULT_REPS_TARGET` |

- **Versões dos legados**: `PROMPT_TEMPLATE_VERSION` (`plan-templates-v1`, `template-selector-v2`) só nomeia os
  templates; não entra no texto do prompt, em cache nem em dedupe. Como esses dois prompts não têm snapshot, o
  sha256 do prompt renderizado pelo código de antes foi fixado em `tests/unit/test_m103_p3_hash_prompts_sem_versao.py`
  (6 entradas cobrindo os ramos) ANTES da extração.
- **Números escritos à mão herdados** (sem mudança de byte, no texto dos dois legados desde antes): "1 a 3 exercícios"
  e "~5-6 séries" por exercício, `stimulus_weight` "entre 0.3 e 0.7", "~4 séries" por exercício e os valores de
  exemplo dos schemas. Não casam padrão da guarda (nem no `.py` antes, nem no template agora) e o módulo é legado
  com remoção prevista (E14 parte 2); ficam como divergência conhecida em vez de ganhar dono novo. Condição de
  saída: somem com o módulo no E14 parte 2; se o legado sobreviver a qualquer mudança de texto desses templates
  (nova versão), os quatro números de domínio passam a ser interpolados do dono (`limits`/`methodology_volume` e
  pesos por papel) na mesma mudança. Os valores de exemplo dos schemas ilustram formato, não regra.
- **Guarda**: as quatro pastas entram em `CONSUMIDOR_DO_PROMPT`. Allowlist inalterada.
- **Testes com frase de prompt** foram para `api/privado/tests/unit/test_periodization_prompt.py` (de
  `test_periodization_prompt_metodologia`, `test_pr16_periodizacao_prompt_e_schema`, `test_m97_f5_periodizacao_objetivo`,
  `test_modo_contagem_volume_l5_geracao`, `test_m76_lote4c_defaults_tetos_prompts`, `test_m98_c5_periodizacao_cardio`)
  e `test_fichas_legado_prompt.py` (novo). No público ficam versões estruturais, que comparam com o fragmento
  renderizado ou com o valor do dono e falham sem pôr o prompt no log (`tests.support.privado.exigir`), e
  `test_m103_p3_prompts_privados.py` (todo fragmento existe e é usado, versão = `core.PROMPT_VERSION`, nenhuma linha
  de template em `api/app`). O teste de integração do cardio (`test_pr16_periodizacao_validador`) passou a conferir o
  campo `cardio_items` no prompt em vez do título da seção.
- **Imagens**: o passo 2b do smoke renderiza também a Periodização (modos A/B × modo de contagem × objetivo), a seção
  de cardio (faixas do TSB × origem) e a ficha legada do Modelo (com e sem dose reduzida) dentro de cada imagem.

**Impacto em produção:** nenhum (bytes idênticos). `index.json` sem diff contra a `develop` (os 10 casos de
`periodization`, inclusive os 4 de cardio); os snapshots passam sem `LLM_PROMPT_SNAPSHOT_WRITE`; `PROMPT_VERSION`
continua `periodization-v14`; os 6 sha256 dos dois legados iguais aos fixados antes da extração; um harness de
paridade (fora do repositório) comparou 2.344 saídas entre o código da `develop` e o novo (modos A/B × modo de
contagem × preferências × tabela/técnicas/metas vazias; objetivo por dia em todas as combinações; cardio em 6 TSB
× 3 origens × RAG vazio/cheio; células com e sem faixa de exercícios; checklist de 1 a 8 dias; ficha do Modelo e
seleção v2 com descanso e RAG variados), todas iguais, com todos os 43 fragmentos renderizados ao menos uma vez.
Modo de falha novo: template ausente na imagem vira `PromptTemplateNotFound` na geração (antes impossível, o texto
estava no código); mitigação: o passo 2b do smoke carrega todo fragmento das 4 pastas e renderiza os ramos dentro de
cada imagem, e o gate do deploy confere os templates.

### 73.4 Prompts da avaliação de série e do card de exercício, e a varredura final — M103 P4 (2026-10-29)

Último lote do M103. Mesmo padrão do §73.1 para a avaliação de série (IA por série do Modo Treino) e para o prompt
de imagem do card de exercício. Nenhum dono nem regra muda: o builder público decide a ordem, os ramos e os dados, e
todo número vem do dono pela tabela de valores. A prosa gerada pelos motores (por exemplo `describe_ruleset`,
`describe_pain_rule`, `category_rule_text`, `WORST_SIDE_RULE_TEXT`, `format_decision`) continua pública, no dono.

| Consumidor | Template(s) (`api/privado/prompts/`) | Valores (código público) |
|---|---|---|
| `exercise_set_evaluation_ai.py` | `set_evaluation/set-eval-v8.md` + 31 fragmentos: séries anteriores e sinais vazios, `.anotacao`, decisão do motor (`.motor`, `.motor_sessao`, `.motor_sem_decisao`, `.nenhuma_decisao`, `.motor_limite`, `.motor_sem_limite`, `.passo`), status (`.status`, `.status_sem_alvo`, `.status_explicar`, `.evento`, `.evento_alerta`, `.evento_informativo`), próxima série (`.proxima_serie`, `.proxima_serie_sem_passo`, `.proxima_serie_uso_carga`, `.proxima_serie_uso_sem_carga`, `.rampa`), `.dor_alta`, regra crítica (`.regra_intensidade`, `.regra_intensidade_proxima_serie`, `.ultima_serie_*`, `.falha_permitida`, `.falha_negada_par`) e escopo (`.escopo_motor`, `.escopo_proxima_serie`). Versão = `PROMPT_VERSION` (`set-eval-v8`, inalterada) | `MUSCLE_PAIN_BLOCK_ABOVE` (motor de recuperação), `FAILURE_RPE` e o teto composto (`training_safety`), `next_set_load` (`describe_*`, `reason_label`, carga e passo), `set_evaluation_categories` (códigos, rótulos, `category_rule_text`), `intensity_status_labels`, `format_kg`/`format_decision`, `goal_prompt_context`, `user_data_rule` |
| `exercise_card_prompt.py` | `exercise_card/exercise-card-v2.*`: um fragmento por bloco (`.intro`, `.formato`, `.cenas`, `.referencia`, `.biomecanica`, `.estilo`, `.evitar`, `.revisao`, `.verificacao_final`) e as linhas de ramo da referência e da revisão (nome em inglês, outros nomes, equipamento ausente/um/alternativas e a regra, carga por convenção `.carga_<convenção>`, lado único, passos, tags, motivo, pedido, fecho) | `BRAND_COLORS` (cores), `BLOCK_*` (títulos), `ILLUSTRATION_ASPECT_RATIO`, `SCENE_GAP_RANGE` e `SCENE_EDGE_MARGIN` (forma da ilustração; o compositor passou a ler `CUT_RANGE = SCENE_GAP_RANGE`, o mesmo `(0.40, 0.60)`), os tetos `MAX_*` e `MAX_PROMPT_CHARS` |
| `workout_plan_ai.py` (achado na varredura) | `intervals_wellness/intervals-wellness-v1.md` + `.sem_dados` (seção do Intervals.icu da ficha legada) | data e valores do wellness; o ramo "dado velho omite a seção" (`wellness_freshness`) |

- **Versões**: `exercise_card_prompt.PROMPT_TEMPLATE_VERSION` (`exercise-card-v2`) e
  `workout_plan_ai.INTERVALS_PROMPT_TEMPLATE_VERSION` (`intervals-wellness-v1`) só nomeiam os templates. A
  `PROMPT_VERSION` do card (`MUSCLEFIT_EXERCISE_CARD_V2_ILLUSTRATION`, gravada no job) não cabe no formato do nome
  e não muda; nenhuma das duas entra no texto.
- **Números no template**: nenhum. As percentagens e a proporção do card, que antes estavam escritas no `.py`,
  agora vêm de constantes nomeadas no próprio módulo, que é o dono da forma pedida à ilustração. Restam "1 a 3
  frases" na tarefa da avaliação de série (formato da resposta, não regra de domínio, como o "2 a 4 frases" do
  P2) e a escala "/10" da dor.
- **Hashes fixados ANTES da extração** (`tests/unit/test_m103_p4_hash_prompts.py`, commits anteriores à
  refatoração): 6 entradas do card, 6 ramos da avaliação de série que os snapshots não cobrem e 5 da seção do
  Intervals.icu.
- **Guarda**: 3 pastas novas em `CONSUMIDOR_DO_PROMPT`. Allowlist inalterada.
- **Testes com frase de prompt** foram para `api/privado/tests/unit/test_set_evaluation_prompt.py` (de
  `test_set_eval_v5_esforco_e_dor`, `test_set_eval_v8_regra_que_casou`, `test_carga_proxima_serie`,
  `test_m76_lote1c_prompts_decisao_motor`, `test_llm_custo_rag_prefixo`), `test_exercise_card_prompt.py` (de
  `test_exercise_card_prompt`) e `test_intervals_wellness_prompt.py` (de `test_m76_lote2a_recuperacao_regra_unica`
  e `test_m52_wellness_idade_maxima`). No público ficam versões estruturais com `exigir`, que comparam com o
  fragmento renderizado ou com o valor do dono.
- **Varredura final** (`test_m103_p4_prompts_privados.py`): nenhuma linha de nenhum template do privado (todas as
  pastas de `prompts/`) aparece em `api/app`, nos testes públicos, em `frontend/src` nem em `docs/`. A comparação
  colapsa espaços e quebras de linha, e a falha mostra só o arquivo e a contagem. Na mesma varredura, dois ecos de
  prompt foram reescritos sem copiar a frase: o docstring de `domain/cardio_envelope.py`, que repetia uma linha do
  prompt de cardio, e a `descricao` do caso `feijoada_completa` em `tests/eval/casos/meal_parse.json` (o `input`,
  que entra no snapshot, não mudou).
- **Ficaram públicos de propósito** (divergência conhecida, sem mudança de byte):
  - a frase de limitação "toda conclusão precisa citar uma evidência" e as `description` de `ALLOWED_ACTIONS` do
    pacote de insights (`intelligence/dashboard/intelligence_package.py`): fazem parte do contrato da rota pública
    `GET /analytics/dashboard/intelligence-package`;
  - as três `description` do schema de saída da descrição de execução (`exercise_description_ai.OUTPUT_SCHEMA`):
    vão no corpo HTTP como schema;
  - os cabeçalhos curtos de seção e as molduras do RAG nos builders.

  Condição de saída: entram no template na próxima mudança de texto da feature, com versão nova.

**Impacto em produção:** nenhum (bytes idênticos). `index.json` sem diff contra a `develop` (os 10 casos de
`set_evaluation`). Os snapshots passam sem `LLM_PROMPT_SNAPSHOT_WRITE`. Os 17 sha256 fixados antes da extração
continuam iguais. Um harness de paridade, fora do repositório, comparou 6.961 saídas entre o código da `develop` e o
novo, todas iguais, e renderizou ao menos uma vez cada um dos 59 fragmentos das duas features principais. As saídas
cobrem:
- avaliação de série: 6 payloads (sem peso, dor alta/baixa, anotação, lados) × 6 motores (sem motor, sem decisão,
  decisão com e sem carga, força, deload) × 4 composições × 6 leituras × 4 regras da próxima série × nomes de
  técnica;
- card: 4 contextos × 4 instruções × 3 motivos.

Modo de falha novo: um template ausente na imagem vira `PromptTemplateNotFound` na avaliação de série, na geração
do card ou na ficha legada; antes era impossível, porque o texto estava no código. Mitigação: o passo 2b do smoke
carrega todo fragmento das 3 pastas e renderiza os ramos dentro de cada imagem.

## 74. Provedor Anthropic por feature no cliente LLM e preço do Claude (2026-10-30)

PR 1 da migração da IA de texto para o Claude. Nenhuma feature muda de provedor: o padrão de todas segue `openai`
e o corpo HTTP da OpenAI é o de antes (snapshots intactos). A virada é por feature, depois do eval.

**Donos.**

| Regra | Dono | Consumidores |
|---|---|---|
| Preço por modelo de toda chamada de texto (OpenAI e Claude), faixa de prompt longo do Haiku 5.5 (prompt acima de `HAIKU_LONG_PROMPT_THRESHOLD_TOKENS` paga `MODEL_LONG_PROMPT_PRICES`; o "prompt" conta toda a entrada, inclusive cache), normalização do snapshot datado (`-AAAA-MM-DD` da OpenAI e `-AAAAMMDD` da Anthropic) e o custo | `domain/nutrition/ai_usage.py` (`MODEL_PRICES_USD_CENTS_PER_MTOKEN`, `MODEL_LONG_PROMPT_PRICES`, `ANTHROPIC_PRICE_SOURCE_URL`, `ANTHROPIC_PRICES_CHECKED_ON`, `price_for_model`, `estimate_cost_usd`) | `nutrition_ai_quota` (como antes), `periodization/logs.estimate_cost_usd` (só a escala DECIMAL(8,4) da coluna) |
| Provedor por feature, modelo e esforço padrão do Claude, teto `max_tokens` (o mesmo `LLM_MAX_OUTPUT_TOKENS`), códigos de erro e elegibilidade ao fallback | `llm_anthropic.py` (`LLM_FEATURES`, `DEFAULT_ANTHROPIC_MODEL`, `DEFAULT_ANTHROPIC_EFFORT`, `fallback_eligible`) + `openai_client._dispatch` | `infra/llm_providers.tf` (lista de features com paridade em `test_llm_provider_infra_paridade.py`) |
| Adaptação do schema strict ao subconjunto da Anthropic (limites de 16 uniões e 24 opcionais, com fonte) | `llm_anthropic_schema.py` | `llm_anthropic.call_structured` |

Preços (página oficial da Anthropic, 2026-10-30), em centavos por 1 milhão de tokens (entrada / lida do cache /
saída): `claude-sonnet-5-5` 200 / 10 / 1000; `claude-haiku-5-5` 10 / 1 / 50 até 100.000 tokens de prompt e
50 / 5 / 250 acima. O acerto de cache do Sonnet 5.5 é 0,05x da entrada (10, não 20). A escrita de cache (1,25x) entra
pelo preço cheio da entrada: o custo do Claude com cache é estimativa por baixo (só as features com `cache_prefix`).

**Decisão 7 do §54 resolvida para a periodização.** `periodization/logs.estimate_cost_usd(model, tokens_in,
tokens_out)` passa a consumir o dono. Antes lia `OPENAI_PRICE_IN/OUT_PER_MTOKEN`, que a infra nunca publicou.
Os cards (`IMAGE_PRICES_USD`, imagem) continuam no `openai_client`, porque são outra unidade (preço por imagem).

**Guarda.** Em `cota_ia_nutricao`, a tabela copiada agora casa também com `"claude-…": <número>`, e as constantes
novas (`MODEL_LONG_PROMPT_PRICES`, `HAIKU_LONG_PROMPT_THRESHOLD_TOKENS`) não podem ser redefinidas fora do dono.
Allowlist inalterada.

**Prompts.** Nenhum byte muda. Os builders de insights, análise por IA e periodização ganham uma função irmã que
devolve o começo estável do mesmo prompt (`insight_cache_prefix`, `report_ai.build_cache_prefix`,
`build_prompt_cache_prefix`, por `prompt_store.render_prefix`, que corta no primeiro placeholder do pedido). O cardio usa
`cache_prefix_of`, que corta no separador dos dados. O prefixo só vira bloco com `cache_control` no Claude.

**Impacto em produção:** com os padrões, nenhum comportamento muda. As features seguem na OpenAI com o mesmo corpo
HTTP (`test_llm_corpo_http_snapshot.py` e `test_llm_prompt_renderizado_snapshot.py` passam sem regravar).
`llm.usage`/`llm.error`/`llm.output_incomplete` ganham `provider=openai`. `template_generation_logs.cost_usd` deixa de
ser sempre NULL. Medido em 2026-10-30, só com leitura: em produção, `SELECT COUNT(*), COUNT(cost_usd) FROM
template_generation_logs` devolveu 8 linhas, nenhuma com custo. A Lambda `musclefit-plan-generator` tem 21 envs, nenhuma
`*PRICE*`. Só `periodization/logs.py` (escrita) cita a coluna dessa tabela em `api/app`. Em `frontend/src`, `cost_usd`
aparece só nos cards ilustrados, que são outra tabela. É uma coluna de auditoria sem leitor, e não há backfill: as linhas
antigas ficam NULL. A infra importa o secret existente
`musclefit/claude-api-key` (só tags mudam), dá `GetSecretValue` nele às 3 roles que já leem o da OpenAI e publica as
envs `LLM_PROVIDER_<FEATURE>=openai` e `ANTHROPIC_API_KEY_SECRET_ARN` em api-core, plan-generator, cardio-agent e
workout-ai.

## 75. Virada de 6 features para o Claude Haiku 5.5 e escrita no cache no preço do Claude (2026-10-31)

**Dono do preço inalterado:** `domain/nutrition/ai_usage.py`. Entram `ANTHROPIC_CACHE_WRITE_5M_MULTIPLIER` (1,25× a
entrada, "5-minute cache write" da página oficial de preços), o campo `ModelPrice.cache_write_cents` (None na OpenAI,
que não cobra a escrita à parte) e o argumento `cache_write_tokens_in` de `estimate_cost_usd`. A leitura de cache do
Sonnet 5.5 continua 0,05× a entrada (US$ 0,10/MTok), reconferida na fonte oficial em 2026-10-31. Quem consome:
`tests/eval/resumo_uso_eval.py` (soma o `cache_write_tokens` do `llm.usage`). Nenhum consumidor de produção recebe hoje
tokens de escrita: as features com cache não gravam custo (`llm.usage` só leva tokens), e o `cache_control` saiu do
caminho Anthropic (`llm_anthropic.ANTHROPIC_CACHE_CONTROL_FEATURES` vazio). Conta de equilíbrio, com os valores de hoje do dono: cada escrita custa 0,25× a entrada a mais e cada leitura economiza 0,9× (Haiku; 0,95× no Sonnet 5.5), então o cache se paga com ≥ 0,28 leitura por escrita. Com N chamadas/dia independentes, P(outra em 5 min) = 1 − exp(−N·5/1440) ≥ 0,28 exige N ≈ 95 por feature e versão de prompt; produção teve ≤ 1 em 14 dias.

**Fora dos motores:** o provedor por feature (`infra/llm_providers.tf`) e o modelo gravado por feature
(`openai_client.TextResult`) são configuração do cliente LLM, sem regra de domínio. Nenhum prompt muda. Guarda e
allowlist inalteradas.

**Impacto em produção:** `ai_usage_log.cost_usd` e `template_generation_logs.cost_usd` não mudam (nenhuma das duas
recebe tokens de escrita no cache). O efeito da virada (modelo gravado, impressão do resumo de treino) está no PR.

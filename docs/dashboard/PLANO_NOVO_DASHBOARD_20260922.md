# Novo Dashboard MuscleFit — Diagnóstico e Plano

Base: `c:\novo-dashboard.md` (brief). Repositório `C:\muscle_fit`, branch `develop` (HEAD `429d10a`). Todo caminho citado foi verificado no código atual; a doc `MUSCLE_FIT.md` (2026-09-12) está 334 commits atrás e foi usada só como mapa inicial.

Decisões do usuário (2026-09-22): (1) consolidar as telas analíticas antigas no hub `/dashboard/*` e aposentá-las após paridade; (2) criar `equipment_exercises.load_convention` (NULL = total, compatível com D12); (3) eventos de contexto com registro manual + derivados.

Regras de operação (CLAUDE.md): nada aplicado à mão na AWS; toda implementação delegada a agentes `fullstack`/`aws`; PRs contra `develop`; sem tocar `C:\openclaw-dev-agent`.

---

## 1. Resumo executivo

**O que já existe (muito mais do que o brief supõe):**
- Hub `/dashboard/*` v2 com 6 sub-rotas (`treinos`, `progressao`, `recovery`, `musculos`, `ciclo`, `engine`) e filtro na URL (`frontend/src/lib/dashboard-filters.ts`), servido por 14 rotas `/analytics/dashboard-*` em `api/app/analytics.py` na Lambda `musclefit-engines`.
- Modelo canônico normalizado já é a fonte de verdade: `exercise_catalog`, `exercise_muscle_contributions(role, stimulus_weight, fatigue_weight)`, `equipment_exercises` N:N, `exercise_aliases`, `exercise_external_mappings(provider='AMAZFIT')`, `exercise_legacy_id_map`. `exercise_zepp_mappings` é legado, só recebe escrita em `POST /exercise-mapping-catalog/import` (deprecado, com telemetria).
- Motores determinísticos com versão gravada: progressão v2 (`api/app/domain/progression_engine.py`, `ENGINE_VERSION="progression-v2.0.0"`, tabelas `progression_group_states/tracks/decisions`), geração Modo A/B (`api/app/generation/mode_a.py`, `mode_b.py`), prescrição congelada (`workout_plan_exercises.prescription_json` + `engine_version`, migração 037).
- `set_type` canônico (`api/app/set_types.py`) com aquecimento/ajuste excluídos dos agregados; séries efetivas = Σ `stimulus_weight` (`volume_service.py:683`), sem multiplicar carga nem RPE; fadiga como dimensão separada (`fatigue_weight` + dose com RPE).
- Recuperação v2 `fullbody-no-reps-v1` (`muscle_recovery_service.py`), e1RM Epley+RIR (`estimated_1rm`, `:143`), 5 estados de volume (`classify_weekly_volume_status`).
- Logs estruturados próprios (`api/app/observability.py`), migrações versionadas (`api/db/migrations/`, até 043), testes unit/integration (dois backends).

**Principais gaps:**
1. Sem contrato semântico: zero `response_model` no backend; payloads `dict` livres; campos deprecados vazam (`rpe_anticipated`).
2. Sem timezone (tudo naive, `CURDATE()` em UTC vs `local_date` do usuário).
3. Sem convenção de carga por par (regra D12 implícita); sem comparabilidade formal por exposição (progressão por e1RM em janela de dias, `classify_progression_trend` exige 3 sessões).
4. Sem eventos de contexto, sem cintura/medidas, sem disposição subjetiva; HRV/FC repouso só via `intervals_wellness`.
5. Aderência fraca: `completion_rate_30d = workouts_30d / planned_plans_30d` (contagem, não sessão↔plano); sessões agrupadas por string de nome no frontend.
6. Regras duplicadas no cliente (limiar 9 séries em `/treinos`, janelas de semana-calendário em `/volume-grupamentos`, 5 cópias de rótulos de status), zeros para dado ausente, 6 conjuntos de período.
7. Auditoria do motor só em `workout_plans.raw_payload` (JSON) e por 6h no DynamoDB se não salvar.
8. Alertas sem cooldown/persistência; `SIBLING_MUSCLE_GROUP_IDS=(6,7)` hardcoded; severidade `CRITICAL` para treino normal.
9. `/insights/generate` recebe contexto montado pelo frontend + RAG inteiro; `DashboardAggregatorService` não propaga `user_id` (isolamento).
10. `exercise_zepp_mappings` ainda tem FKs vivas (`exercises.mapping_id`, `gym_equipment.mapping_id`, `equipment_exercise_mappings`).

**Sequência recomendada:** Fundação (contratos + timezone + comparabilidade + data-quality + rotas) → MVP (overview de 6 blocos, exercício, músculos, treinos, ciclo, corpo, recuperação, insights determinísticos) → Fase 2 (contexto, alertas com cooldown, cardio, aposentar telas antigas) → Fase 3 (engine runs, pacote para PT/LLM).

**Status: READY TO IMPLEMENT** (ver §19 — nenhuma pergunta bloqueante restante).

---

## 2. Evidências inspecionadas

Backend (`api/app/`): `analytics.py` (rotas `/analytics/dashboard-*`, `MUSCLE_GROUP_CASE:106-129` legado), `dashboard_modules.py`, `dashboard_intelligence.py`, `intelligence/volume_service.py` (2.752 l.), `intelligence/muscle_recovery_service.py`, `intelligence/progression_service.py`, `intelligence/dashboard_aggregator_service.py`, `intelligence/dashboard_alert_service.py`, `intelligence/recovery_projection.py`, `intelligence/muscle_group_progression.py`, `intelligence/report_data_service.py`, `intelligence/cardio_context_service.py`, `domain/progression_engine.py`, `domain/progression_evaluator.py`, `domain/training_history.py`, `domain/prescription.py`, `domain/training_safety.py`, `domain/effective_intensity.py`, `progression_state.py`, `progression.py`, `progression_service.py` (v1), `generation/mode_a.py`, `generation/mode_b.py`, `generation/activation_window.py`, `generation/__init__.py`, `plan_generation_core.py`, `workout_sessions.py`, `services.py` (`upsert_activity:356`, `resolve_mapping_id:174`, `resolve_equipment_id:232`, `create_workout_plan:2383`, `_freeze_plan_prescriptions:2283`), `set_types.py`, `weight_units.py`, `exercise_mapping_canonical.py`, `exercise_mapping_catalog.py:214,858`, `devices.py`, `insights_ai.py`, `insights_core.py`, `openai_client.py`, `observability.py`, `security.py`, `async_jobs.py`, `db_dataapi.py:86-95` (limite 1 MB), `main_engines.py`, `engines_client.py`, `intervals_*.py`.
Schema (`api/db/migrations/`): `001_schema.sql`, `002_reestruturacao.sql`, `004`, `005`, `006`, `009`, `010`, `011`, `012`, `016`–`019`, `021`, `023`–`027`, `029`, `031`–`043`.
Frontend (`frontend/src/`): `app/(app)/dashboard/page.tsx` (visão geral, 513 l.), `dashboard/{treinos,progressao,recovery,musculos,ciclo,engine}/*Client.tsx`, `app/(app)/treinos/page.tsx`, `volume-grupamentos/VolumeGrupamentosClient.tsx`, `recuperacao-muscular/page.tsx`, `exercicios/page.tsx`, `evolucao-corporal/page.tsx`, `insights/page.tsx`, `lib/dashboard-filters.ts`, `lib/dashboard-v2-types.ts`, `lib/analytics-types.ts`, `lib/api.ts`, `lib/async-job-poll.ts`, `lib/muscle-readiness.ts`, `components/dashboard/*`, `components/charts/chartTheme.ts`, `components/MuscleBodyMap.tsx`, `theme.ts`, `app/providers.tsx`.
Infra/testes: `infra/api_gateway.tf`, `nginx/local-dev.conf`, `infra/dynamodb.tf`, `infra/cloudfront.tf`, `api/tests/unit/test_paridade_rotas_engines.py`, `api/pytest.ini`, `Makefile`, `frontend/vitest.config.ts`.
Docs: `docs/refatoracao-motores/{REQUISITOS,PLANO}_20260912.md`, `ATIVACAO_MOTORES_DETERMINISTICOS.md`, `docs/local-dev.md`, `docs/brand/brand-guide.md`.

Pistas do brief confirmadas/negadas: `VolumeService.get_muscle_activation_for_exercises` ✔ (`volume_service.py:1275`); `MuscleRecoveryService` ✔; `progression_service` ✔ (duas: v1 `api/app/progression_service.py` e trend `intelligence/progression_service.py`); `generation_summary` ✔ (chave em `workout_plans.raw_payload`, sem coluna); `DashboardAggregatorService` ✔ (`intelligence/dashboard_aggregator_service.py:24`); `/api/insights` ✘ (existe `POST /insights/generate` 202, `main.py:252`); `DashboardClient.tsx` ✘ (visão geral em `dashboard/page.tsx:104`); `exercise_zepp_mappings` ✔ tabela física legada.

---

## 3. Mapa do estado atual

- **Frontend**: Next 14 export estático + Mantine 7 + React Query 5 (sem `defaultOptions`) + recharts 3. Hub `/dashboard/*` + 6 telas antigas com regras próprias. Sem skeleton, sem teste de componente (vitest só em `lib/*.test.ts`).
- **Backend**: FastAPI, SQL cru via `db.py` (Data API em prod / PyMySQL em dev). Duas Lambdas HTTP: `api-core` (CRUD, sessões, jobs) e `engines` (`/analytics/*`, sem OpenAI). Rotas do `engines` precisam existir em 3 lugares (router, `infra/api_gateway.tf`, `nginx/local-dev.conf`) — guardado por `test_paridade_rotas_engines.py`.
- **Banco**: Aurora Serverless v2 MySQL; limite 1 MB por resultado Data API; índices por migração; `user_id` nullable em várias tabelas de dado.
- **Importação**: `POST /activities` (replace destrutivo por `(source_id, external_id, user_id)`), sessão ao vivo `/workout-sessions/*` (sem idempotência, `set_number=MAX+1`), Intervals.icu sync agendado (wellness: HRV, FC repouso, TSB, peso), sono/EPOC/bioimpedância manuais. Sem app Zepp nem OpenClaw no repo (consumidores externos via token).
- **Agente/LLM**: OpenAI Responses API; `/insights/generate` (texto livre), cardio agent (persistido), periodização (structured output). Sem pacote estruturado compartilhado.
- **Dashboard atual**: ver §2; alertas em `DashboardAlertService` (5 tipos × INFO/WARNING/CRITICAL, sem persistência).

---

## 4. Source of Truth Map

| Domínio | Fonte canônica atual | Escritores | Leitores | Legado/fallback | Risco | Plano |
|---|---|---|---|---|---|---|
| Exercício canônico | `exercise_catalog` (+`exercise_legacy_id_map`) | admin catálogo, `resolve_mapping_id` | todos os motores (`COALESCE(elm.canonical_exercise_id, e.mapping_id)`) | `exercise_zepp_mappings` (escrita só em `/import`), FKs de `exercises.mapping_id`/`gym_equipment.mapping_id` ainda apontam à legada | mapa de ids obrigatório em toda query | Novo código lê **só** via helper único (`training_history.py`/`_resolve_canonical_exercise_id`); reapontar FKs = migração própria fora deste plano (registrar M) |
| Músculos/regiões | `muscle_groups` (20 grupos; costas 6/7, deltoide sem subdivisão) | admin | todos | `MUSCLE_GROUP_CASE` regex em 3 rotas legadas | rotas legadas classificam por nome | Não usar nas rotas novas; aposentar com as telas antigas |
| Contribuição (role, stimulus, fatigue) | `exercise_muscle_contributions` | admin catálogo | `VolumeService._resolve_contributions` | fallback `_group_contributions` (SECONDARY 0.50) vs recovery `participation` (0.30) | duas defaults | Data-quality lista exercícios sem contribuição; dashboard só usa pesos explícitos e marca fallback |
| Equipamento / par | `equipment`, `equipment_exercises` (N:N), `gym_equipment` (cache), `equipment.load_increment_kg` | admin, `gym_equipment_sync` | recomendador, geração | `equipment_exercise_mappings` legada | sem convenção de carga | **Nova coluna `equipment_exercises.load_convention`** (§14) |
| Aliases/Zepp | `exercise_aliases`, `exercise_external_mappings('AMAZFIT')` | admin, `/mapping-canonical/associate` | `match_mapping` | — | alias sem índice (029) | Sem mudança; data-quality expõe `/mapping-canonical/unmapped` |
| Séries | `exercise_sets` (`set_type`, `rpe`, `rpe_target_effective`, `safety_event`, `muscle_pain`, `joint_pain`) | importação, sessão ao vivo | agregados via `set_types.sql_not_preparatory()` | `set_type` livre no banco, normalizado na leitura | valor desconhecido vira working | Data-quality: séries com `set_type` fora do vocabulário |
| Sessões | `activities` (`local_date`, `started_at`, `session_status`, `workout_plan_id`) | idem | todos | `session_status NULL` = legado | sem timezone | **`users.timezone`** + data efetiva = `local_date` |
| Plano/ficha | `workout_plans` + `workout_plan_exercises` (`prescription_json`, `engine_version`, `mapping_id`, `preferred_equipment_id`) | geração + usuário | modo treino, engine audit | `raw_payload` JSON | sem versão de ficha | Fronteira de versão = `workout_plans.id`/`template_id`+`template_slot_sequence`; sem migração |
| Template/ciclo | `workout_plan_templates/slots/items`, `assignments` | admin/PT | `dashboard-cycle`, geração | ~~assignments sem histórico~~ (resolvido: 049) | ~~troca de template não deixa rastro~~ | **M23/049**: `unassigned_at` dá o intervalo `[assigned_at, unassigned_at)` de cada vigência; `template_assignments.resolve_assignment_at` responde "qual era o Modelo nesta data" e é a fronteira de versão do plano. Uma vigência por usuário é invariante do banco |
| Metas | `training_types` + `training_type_muscle_group_targets(min/target/max)` | admin | `_build_muscle_status_items`, Modo A | fallback 6/9/27 | meta vigente por data não versionada | Expor `target_source`; registrar M para versionar metas |
| Progressão | v2: `progression_group_states/tracks/decisions` | `on_session_finish` | `/progression/*`, geração | v1 `suggest_load_and_reps`, `classify_progression_trend` | duas implementações | Dashboard usa `progression_decisions` como evidência e novo `exposure_service` para comparabilidade |
| Recovery | calculado por `MuscleRecoveryService` + `muscle_recovery_coefficients` + `intervals_wellness` | — | `dashboard-recovery`, geração | coeficiente por nome (VARCHAR) | — | Reusar; adicionar "sinais concordantes/divergentes" |
| Corpo | `body_composition`, `daily_biometrics`, `intervals_wellness.weight_kg` | manual/Intervals | `BodyCompositionTrendService` | — | sem cintura/fotos | **Nova `body_measurements`** |
| Cardio | `intervals_activities`, `activity_intervals_link`, `activity_hr_zones`, `cardio_recommendations` | sync/agent | `/integrations/intervals/*`, cardio context | — | — | Reusar (Fase 2) |
| Contexto | **não existe** | — | — | — | — | **Nova `context_events`** + derivados |
| Engine runs | `generation_runs` (migração 054, **M16**) + `workout_plans.raw_payload.generation_audit`, `template_generation_logs` (gera MODELO, não ficha), `progression_decisions`, DynamoDB jobs (6h) | motores | `dashboard/engine` + `dashboard/engine/plans/{id}` (**F3.1**); `dashboard-engine-audit` passou a legado com telemetria | gerações anteriores ao M16 (lidas do `raw_payload`, com a limitação declarada) | resolvido pelo M16: persiste na geração, não no save | — |

---

## 5. Data lineage

```
Relógio/Zepp/OpenClaw ──POST /activities (replace por external_id)──┐
Modo Treino/Relógio ──/workout-sessions/{start,exercises,sets,finish}─┤→ activities / exercises / exercise_sets
Intervals.icu (EventBridge→lambda_intervals_sync) ──→ intervals_activities, intervals_wellness, activity_intervals_link
Manual ──→ body_composition, sleep_metrics, manual_effort_metrics, [novo] body_measurements, context_events
         ↓ normalização canônica: resolve_mapping_id → exercise_catalog (via exercise_legacy_id_map), resolve_equipment_id → gym_equipment
         ↓ set_types.normalize (leitura) · compute_exercise_stats (agregados sem warmup/feeler)
         ↓ serviços de domínio: VolumeService · MuscleRecoveryService · training_history DTO · progression_engine v2 · [novo] exposure/comparability · [novo] adherence · [novo] data_quality
         ↓ agregação: [novo] api/app/intelligence/dashboard/*  (envelope semântico + metric_version)
         ↓ API: /analytics/dashboard/* (engines)  ·  /context-events, /body-measurements (api-core)
         ↓ UI: hub /dashboard/*  ·  Personal Trainer/LLM: pacote /analytics/dashboard/intelligence-package
```

Gaps de dado e classificação (derivável / nova coluna / backfill):
- Timezone: nova coluna `users.timezone` (default `America/Sao_Paulo`), sem backfill de histórico (datas já são `local_date`).
- Convenção de carga: nova coluna `equipment_exercises.load_convention` NULL→`total`; sem reescrita de histórico; data-quality lista pares sem convenção.
- Sessão pulada: **derivável** (slot do template sem `workout_plans`/`activities` no intervalo esperado) — não criar coluna.
- Exercício substituído: **derivável** (`exercises.workout_plan_exercise_id` presente e `mapping_id` ≠ `wpe.mapping_id`).
- Exercício interrompido: **derivável** (séries working < `sets_count` prescrito em sessão `done`).
- Exercício pulado: **derivável** (item da ficha sem `exercises` em sessão `done`).
- Eventos de contexto: nova tabela `context_events`.
- Cintura/medidas: nova tabela `body_measurements`.
- Disposição/dor global: nova tabela `daily_checkins` (**entregue na F2.2**, migração 050).
- Cooldown de alertas: nova tabela `dashboard_alert_states` (**entregue na F2.2**, migração 051).
- Versão de fórmula: constante em código (`metrics_registry.py`), emitida no payload — sem tabela.

---

## 6. Metric Dictionary (v1)

Todas com `metric_version` emitido no payload; owner = serviço backend; frontend não recalcula.

| Nome técnico / exibido | Pergunta | Fórmula (versão) | Fonte | Janela | Comparabilidade | Exclusões | Qualidade | Limitação | Owner |
|---|---|---|---|---|---|---|---|---|---|
| `physical_sets` / Séries físicas | quantas séries fiz | count(`exercise_sets`) working (`sql_not_preparatory`) — `sets-v1` | exercise_sets | qualquer | sempre | warmup, feeler | HIGH | não pondera músculo | VolumeService |
| `effective_sets_stimulus` / Séries efetivas | volume chegou ao músculo? | Σ working × `stimulus_weight[role]` — `effective-sets-v1` (regra vigente `volume_service.py:683`) | exercise_muscle_contributions | sessão/ciclo/7d/28d | sempre | idem + exercícios sem contribuição (contados à parte) | HIGH se todas contribuições explícitas; MEDIUM se algum fallback | não mede intensidade | VolumeService |
| `effective_sets_fatigue` / Fadiga equivalente | custo de recuperação | Σ working × `fatigue_weight` — `fatigue-sets-v1` | idem | idem | idem | idem | idem | dimensão distinta de estímulo | VolumeService |
| `direct_sets` / `indirect_sets` / `stabilizer_sets` | direto vs indireto | contagem por role — `role-sets-v1` | idem | idem | — | — | HIGH | STABILIZER ≠ SECONDARY (mostrar separado) | VolumeService |
| `weekly_volume_status` / Status vs meta | abaixo/adequado/acima | `classify_weekly_volume_status(actual_weekly, min, target, max)`; `actual_weekly = effective × 7/dias_janela` — `volume-status-v1` (tol. 5%) | + `training_type_muscle_group_targets` | ciclo ou 7d rolling | requer meta (`target_source ≠ fallback_generic`) | — | LOW se fallback genérico | 1 semana isolada não conclui — exigir ≥ 2 ciclos p/ alerta | analytics `_build_muscle_status_items` |
| `muscle_frequency` / Frequência e intervalo | estímulo distribuído? | nº sessões com direct_sets>0 na janela; mediana das horas entre estímulos — `freq-v1` (novo) | activities, contribuições | 28d | — | sessões só indiretas | HIGH | — | novo `muscle_summary_service` |
| `exposure` / Exposição | última vs anterior | sessão com o mesmo `comparability_key` (§8) — `exposure-v1` (novo, sobre `domain/training_history.py`) | exercises, exercise_sets, equipment_exercises | por exposição | só mesma chave | warmup/feeler | HIGH com ≥2 exposições comparáveis | — | novo `exposure_service` |
| `e1rm` / e1RM (estimativa) | força estimada | `load × (1 + (reps + max(0,10−rpe))/30)` — `epley-rir-v1` (vigente, `domain/e1rm.estimated_1rm`) | exercise_sets working com carga+reps | por exposição | mesma chave; reps ≤ 12 (`E1RM_MAX_RELIABLE_REPS`) | sem RPE → e1RM `null` (não inventar, `MissingRpePolicy.SKIP`). **Exceção** (decisão de 2026-10-06, meio-termo): o Desempenho por grupo (`muscle_group_performance`) assume RIR 0 nas séries sem RPE (`MissingRpePolicy.RIR_ZERO`) | MEDIUM sempre (`ESTIMATED`) | não vale p/ assistido/bodyweight sem carga | `domain/e1rm.py` (M76, Lote 3a) |
| `exercise_progression_status` | estou evoluindo neste exercício? | regras §8.2 → PROGRESSING/STABLE/PLATEAU_SUSPECTED/REGRESSING/INSUFFICIENT_DATA/NOT_COMPARABLE — `progression-status-v1` (novo; usa evidências de `progression_decisions` quando existirem) | exposure + progression_decisions | últimas 2–5 exposições | mesma chave | — | HIGH ≥3 exposições, MEDIUM 2, INSUFFICIENT <2 | não é diagnóstico de platô, é suspeita | exposure_service |
| `tonnage` / Tonelagem | contexto | Σ reps×kg working — `tonnage-v1` | exercise_sets | por exposição | só mesma chave | — | HIGH | nunca proxy de hipertrofia; só no drill-down | VolumeService |
| `adherence` / Consistência | cumpri o planejado? | sessões `done` com `workout_plan_id` / slots esperados no ciclo; por item: concluído/pulado/substituído/interrompido (derivados §5) — `adherence-v1` (novo); `adherence-v2` desde 2026-10-02: ciclo de um dia por slot | activities, workout_plans, templates | ciclo, 28d | requer template atribuído | treinos avulsos contam como extra, não como planejado | LOW sem template | — | novo `adherence_service` |
| `readiness` / Prontidão muscular | posso treinar este grupo? | modelo `fullbody-no-reps-v1` (vigente) | recovery overview | agora / data-alvo | — | — | conforme `build_readiness_contract` | 72h teto; INFERRED | MuscleRecoveryService |
| `recovery_signals` / Sinais de recuperação | estou recuperando? | concordância entre: performance_ratio, dor, sono, HRV vs baseline pessoal (mediana 28d), FC repouso vs baseline, TSB — `recovery-signals-v1` (novo) | overview, sleep_metrics, intervals_wellness, daily_checkins (F2.2: os quatro escores entram como sinais SUBJETIVOS, com baseline pessoal própria, e contam UMA vez na concordância — quatro respostas do mesmo formulário não são quatro observações independentes) | 7d vs 28d | baseline ≥ 14 pontos | — | por nº de sinais presentes | associação, não causa | novo `recovery_signals_service` |
| `body_weight_ma7` / Peso (média 7d) | tendência de peso | média móvel 7d de `weight_kg` (body_composition ∪ daily_biometrics ∪ intervals_wellness, prioridade nesta ordem por dia) — `weight-ma-v1` (novo) | 3 tabelas | 28/90d | — | dias sem pesagem não viram 0 | HIGH ≥4 pesagens/semana | — | BodyCompositionTrendService (estender) |
| `waist_cm` / Cintura | recomposição | última medida com `protocol_ok` — `waist-v1` (novo) | body_measurements | 28/90d | mesmo `site` | fora de protocolo → qualidade LOW | — | — | novo `body_service` |
| `recomposition_signal` | compatível com recomposição? | peso estável (±1%) + cintura ↓ (≥1 cm) + ≥50% exercícios comparáveis PROGRESSING/STABLE em 28d — `recomp-v1` (INFERRED) | acima | 28d | — | — | LOW/MEDIUM | nunca "prova" | body_service |
| `data_quality` | dá pra concluir? | contagens por regra (§8.3) — `dq-v1` | várias | janela | — | — | — | — | novo `data_quality_service` |

---

## 7. Question Coverage Matrix

| Pergunta | Evidência mínima | Comparação | Regra de decisão | Resposta visual | Drill-down | Ação possível |
|---|---|---|---|---|---|---|
| Estou evoluindo? | ≥2 exposições comparáveis em ≥3 exercícios | exposição atual vs anterior; 28d vs 28d anteriores | contagem de exercícios por status (sem score único) + aderência + tendência corpo + DQ | bloco Progressão: barras empilhadas por status + lista | `/dashboard/exercicios/:id` | nenhuma automática; sugestão por exercício |
| Músculos atrasados/excesso | meta configurada + ≥2 ciclos | efetivo vs min/target/max por ciclo e rolling 7d | status v1 em ≥2 ciclos consecutivos; direto/indireto separados; frequência | tabela + barra min/target/max | `/dashboard/musculos/:id` → exercícios → sessões → séries | "priorizar grupo" apenas como sugestão |
| Estou recuperando bem? | readiness + ≥1 sinal objetivo com baseline | vs baseline pessoal 28d | sinais concordantes ≥2 → ATTENTION; divergentes → mostrar ambos | painel de sinais (concordam/divergem) + timeline | `/dashboard/recovery` | nenhuma; texto "reavaliar após X" |
| Próxima ação | qualquer regra §12 disparada | conforme regra | prioridade por severidade + confiança; máx. 2 | cards Observação/Evidência/Comparação/Confiança/Alternativas/Ação/Reavaliar | link para a tela de origem | usuário decide; nada muda automaticamente |
| Cumpri o planejado? | template atribuído | slots esperados vs sessões done | adherence-v2 | bloco Consistência + `/dashboard/ciclo` | sessão → itens (concluído/pulado/substituído/interrompido) | — |
| Volume chegou ao músculo certo? | contribuições explícitas | planejado (template_slot_targets) vs realizado (effective) vs projetado (slots pendentes) | três colunas nunca somadas na mesma métrica exibida (status só do realizado; projetado à parte) | `/dashboard/musculos` | exercício que mais contribuiu | — |
| Progresso em condições comparáveis? | chave de comparabilidade igual | exposição | §8 | `/dashboard/exercicios/:id` | séries de origem | — |
| Frequência coerente com o ciclo? | template + histórico | intervalo entre estímulos vs recovery estimado | `get_consecutive_day_recovery_checks` + freq-v1 | `/dashboard/ciclo` | — | — |
| Motor gerou conforme metas? | `raw_payload.generation_audit` ou `volume_planning` | planejado vs gerado vs executado | `audit_detail_level` | `/dashboard/engine` | pipeline | — |
| Dados suficientes? | data_quality | — | cada bloco tem `data_quality` + `limitations[]` | badge por bloco + `/dashboard/data-quality` | itens a corrigir | links para cadastro |

---

## 8. Regras de comparabilidade e qualidade

**8.1 Chave de comparabilidade** (`comparability_key`): `(canonical_exercise_id, equipment_id, load_convention)` onde `equipment_id` é o equipamento resolvido (`exercises.equipment_id → gym_equipment.equipment_id`) e `load_convention` vem de `equipment_exercises` (NULL→`total`). Só séries `working`. Exposições com `equipment_id` nulo ficam `NOT_COMPARABLE` salvo quando todas as exposições do exercício têm equipamento nulo (então chave sem equipamento, qualidade MEDIUM). Equivalência entre equipamentos: não existe cadastro hoje → `NOT_COMPARABLE` (registrar M para tabela de equivalência).

**8.2 Regras de status por exercício** (configuráveis em `metrics_registry.py`):
- `INSUFFICIENT_DATA`: < 2 exposições comparáveis nos últimos 90 dias.
- `NOT_COMPARABLE`: última exposição com chave diferente da anterior.
- `PROGRESSING`: (mesma carga ±2% e reps working totais ↑) ou (carga ↑ e reps na faixa prescrita) ou (mesmo desempenho e RPE médio ≤ −0,75) ou e1RM ↑ ≥3% com reps ≤12; `assisted` inverte sinal da carga; `bodyweight` compara só reps.
- `REGRESSING`: e1RM ↓ ≥3% ou reps ↓ com mesma carga em 2 exposições seguidas.
- `PLATEAU_SUSPECTED`: ≥3 exposições sem PROGRESSING e sem REGRESSING.
- `STABLE`: demais.
- Se `progression_decisions` existe para o par, anexar `decision`/`phase` como evidência (não substitui a regra).

**8.3 Qualidade dos dados** (regras do `data_quality_service`): exercícios sem `mapping_id`; mappings sem contribuição ativa; séries com `set_type` fora do vocabulário; pares sem `load_convention`; séries sem RPE (contagem, informativo); sessões `session_status IS NULL` ou `in_progress` > 24h; sessões sem `workout_plan_id` com template atribuído; pesagens duplicadas no dia com fontes divergentes >1 kg; `activities` com `started_at` nulo; grupos com meta ausente. Cada item: `code`, `count`, `severity`, `fix_link`.

**8.4 Semântica**: enum `DataNature = OBSERVED|DERIVED|PLANNED|PROJECTED|ESTIMATED|INFERRED` e envelope `Metric{value, unit, nature, metric_version, data_quality, limitations[]}`; `value: null` quando não calculável (nunca 0).

---

## 9. Arquitetura proposta

- **Novo pacote backend** `api/app/intelligence/dashboard/` (Lambda `engines`, sem OpenAI):
  - `contracts.py` — modelos Pydantic de resposta (primeiros `response_model` do projeto), envelope semântico.
  - `metrics_registry.py` — versões, thresholds, labels técnicos (única fonte; frontend recebe `code` + `label`).
  - `comparability.py`, `exposure_service.py` (sobre `domain/training_history.py`), `adherence_service.py`, `muscle_summary_service.py`, `recovery_signals_service.py`, `body_service.py`, `data_quality_service.py`, `insight_rules.py` (regras determinísticas + cooldown), `intelligence_package.py` (pacote para UI/LLM), `overview_service.py` (compõe os 6 blocos chamando os demais).
  - `time_windows.py` — janelas `cycle | rolling_7d | 28d | 90d | custom` resolvidas com `users.timezone`; substitui `CURDATE()`.
- **Reuso**: `VolumeService` (séries efetivas, overview), `MuscleRecoveryService` (readiness), `estimated_1rm`, `classify_weekly_volume_status`, `resolve_active_training_type_for_user`, `dashboard-cycle`/`engine-audit` existentes, `training_history` DTO, `progression_decisions`.
- **api-core**: CRUD `context_events`, `body_measurements`, `daily_checkins` (F2.2: `/daily-checkins`, upsert por dia), `users.timezone` (perfil). Nenhum deles precisa de entrada em `infra/api_gateway.tf` nem em `nginx/local-dev.conf`: o catch-all da api-core já os cobre.
- **Fluxo**: UI → `/analytics/dashboard/*` (engines) → serviços → SQL com `WHERE user_id = :uid` obrigatório (helper que recusa `user_id=None`).
- **Cache**: nenhum novo por padrão. Custo real está em `get_muscle_group_overview` (lê histórico completo por chamada); mitigar com `time_windows` + consultas por janela e memo por request (`overview_service` chama cada serviço uma vez). Só se latência p95 > 2 s medida, cache curto (60 s) por `(user_id, window)` em memória de Lambda com invalidação por `finish`/import (registrar M).
- **LLM**: `intelligence_package` alimenta `/insights/generate` (substitui contexto montado pelo cliente) e o PT externo; prompt só explica agregados.
- **Segurança/observabilidade**: ownership por `user_id`; `log_event("dashboard.computed", metric_versions, latency_ms, window)`; eventos `insight.emitted`, `insight.suppressed_cooldown`.

---

## 10. Contratos de API

Novas rotas (todas GET, `engines`, `Depends(get_current_identity)`, query comum: `window=cycle|7d|28d|90d|180d|custom`, `date_from`, `date_to`, `template_id?`, `slot_sequence?`, `gym_id?`; exigem entrada em `infra/api_gateway.tf` + `nginx/local-dev.conf`):

| Rota | Pergunta | Resposta (resumo) | Custo |
|---|---|---|---|
| `/analytics/dashboard/overview` | 4 centrais | `{window, timezone, blocks:{consistency, progression, priority_muscles, body, recovery, next_actions}, data_quality, metric_versions}` | 1 overview + 1 history + metas |
| `/analytics/dashboard/exercises/{exercise_id}/progress` | evoluo neste exercício? | `{exercise, comparability_key, status, exposures[] (sets working, e1rm ESTIMATED, rpe, rest, equipment, position_in_session, notes), records[], progression_decisions[], next_target (v2), explanation}` | 1 query por exercício |
| `/analytics/dashboard/muscles` | atrasado/excesso | `{items[]: {group, targets{min,target,max,source}, realized{effective,direct,indirect,stabilizer,physical}, planned, projected, frequency, last_stimulus_hours, readiness, top_exercises[], status, data_quality}}` | overview + templates |
| `/analytics/dashboard/muscles/{id}` | drill-down | + exposições por exercício, sessões e séries de origem | — |
| `/analytics/dashboard/workouts` | cumpri? | sessões com `plan_link`, itens `completed/skipped/substituted/interrupted`, duração, descansos, estímulo/fadiga, RPE, recordes, contexto do dia | paginado `limit/offset` |
| `/analytics/dashboard/recovery` | recupero bem? | readiness por grupo (existente) + `signals[] {code, value, baseline, direction, nature, agrees_with_readiness}` + `assessment{level, uncertainty, context_events[]}` | overview + wellness |
| `/analytics/dashboard/body` | corpo | `weight_ma7[]`, `waist[]`, `bioimpedance[] (ESTIMATED)`, `recomposition_signal (INFERRED)`, `milestones[]` | 3 tabelas |
| `/analytics/dashboard/data-quality` | dá pra concluir? | `{items[]: {code, count, severity, fix_link, examples[]}}` | N queries leves |
| `/analytics/dashboard/insights` | próxima ação | `{insights[]: contrato §12}` (máx. 2 em `next_actions`, todos aqui) | reusa overview |
| `/analytics/dashboard/intelligence-package` | LLM/PT | pacote §13 (≤ 40 KB) | reusa overview — mesmos statements |
| `/analytics/dashboard/cycle`, `/engine-runs/{plan_id}` | reusam `dashboard-cycle` / `dashboard-engine-audit` com envelope | — | — |
| api-core: `GET/POST/PUT/DELETE /context-events`, `/body-measurements`, `/daily-checkins`; `PATCH /users/me` (`timezone`) | entrada | CRUD com `user_id` | — |

Semântica: `null` = não calculável; `0` = observado zero; `data_quality ∈ HIGH|MEDIUM|LOW|INSUFFICIENT`. Sem paginação exceto `workouts`. Rotas `/analytics/dashboard-*` v2 permanecem até o frontend migrar; depois entram na telemetria de legado.

---

## 11. Arquitetura de informação e wireflow

Rotas (hub existente, evoluído): `/dashboard` (6 blocos) → `/dashboard/exercicios/[id]` (novo), `/dashboard/musculos` (+`?group=`), `/dashboard/treinos`, `/dashboard/ciclo`, `/dashboard/recovery`, `/dashboard/corpo` (novo), `/dashboard/contexto` (novo, Fase 2), `/dashboard/engine`, `/dashboard/data-quality` (novo). Telas antigas → `LegacyPathRedirect` após paridade (Fase 2).

Frontend:
- `lib/dashboard-filters.ts` vira filtro global do hub (adiciona `window`, `date_from/to`, `templateId`, `slot`, `gymId`, `equipmentId`, `exerciseId`, `muscleGroupId`, `nature`); visão geral passa a usá-lo; um só conjunto de opções.
- `lib/dashboard-api.ts` — fetchers tipados por endpoint (novo; hoje paths inline) + `staleTime` 60 s no `QueryClient`.
- `components/dashboard/`: `MetricValue` (renderiza `Metric` com badge de natureza/qualidade e "—" para null), `StatusBadge` (única fonte dos rótulos, substitui as 5 cópias), `DataQualityBadge`, `InsightCard`, `SignalsPanel`, `ExposureTable`, `MinTargetMaxBar`, `DashboardSkeleton`, `EmptyState`, `PartialState`.
- Mobile-first (Mantine `base` primeiro), cards empilhados, filtros em `Drawer`, tabelas viram cards abaixo de `sm`, gráficos `LineChart`/`BarChart` do `chartTheme.ts`, sem dual-axis, sem pie. Estados: loading (skeleton), empty, error, partial (`data_quality`), stale (`dataUpdatedAt`).

---

## 12. Insights e alertas explicáveis

Contrato (`contracts.Insight`): `{code, question, status, severity: INFO|ATTENTION|ACTION, answer, evidence[]{metric, from, to, unit, nature}, comparison{basis, window, keys}, data_quality, limitations[], alternative_explanations[], suggested_action, reassess_after, source_screen, fingerprint}`.
Regras v1 (`insight_rules.py`, thresholds no registry, validados contra dados reais antes de fixar): grupo prioritário abaixo do mínimo por ≥2 ciclos; acima do máximo + readiness piorando; exercício `PLATEAU_SUSPECTED` ≥4 exposições; ≥3 exercícios `REGRESSING` em 28d; item de ficha pulado ≥3× quando no fim da sessão; direto ≪ indireto (razão <0,3) em grupo com meta; assimetria 6/7 (mover ids para dados: par configurável no registry); peso estável + cintura ↓ + progressão (recomposição); queda de desempenho com evento de contexto ≤72h (associação); DQ comprometendo (>20% séries sem mapping).
Cooldown (**entregue na F2.2**, migração 051 — DDL final no §14): a `fingerprint` identifica a OBSERVAÇÃO e continua deduplicando na resposta; quem identifica o ALVO, e é o que o cooldown silencia, é a `alert_key` (`insight_rules.alert_key`). O desenho, em cinco decisões:
1. **Chave** = código + o subconjunto de chaves da comparação que identifica o alvo (`COOLDOWN_KEYS_BY_CODE`, uma entrada por código, com guard unitário que exige a decisão escrita). Tupla vazia = alvo global ("o seu treino", "o seu corpo").
2. **Duração** = o `reassess_after` que a PRÓPRIA regra declara, limitado por um piso por severidade e por um teto geral. Não é número novo: a regra já dizia quando faz sentido olhar de novo, e ninguém lia esse campo.
3. **Escalada fura o cooldown**: severidade pior que a da última emissão aparece mesmo dentro do cooldown, e o cooldown recomeça. O inverso não fura — melhora não é alerta, ela aparece pelo cartão deixar de existir.
4. **Episódio**: sem a condição aparecer por `alert_state_resolution_gap_days`, o estado é resolvido (`resolved_at`) e a próxima ocorrência é PRIMEIRO aviso, sem cooldown herdado.
5. **A unidade é o DIA, não a requisição**: abrir a visão geral já é uma leitura (ela emite `next_actions`); se o cooldown contasse por requisição, `/dashboard/insights` aberta em seguida apareceria vazia. A segunda leitura do mesmo dia devolve exatamente a primeira resposta (motivo lido de `last_reason`).

Piso, teto e intervalo de resolução são **decisão de produto** — propostos em `metrics_registry.THRESHOLDS` com a medição ao lado (piso INFO 14d / ATTENTION 7d / ACTION 3d, teto 60d, resolução 30d), pendentes de confirmação do usuário. A escrita acontece DENTRO da leitura (3 statements: SELECT do estado, varredura de episódios vencidos, upsert multi-linha) e falha de escrita não derruba a tela. `DASHBOARD_ALERT_COOLDOWN=0` desliga sem deploy. Antes da 051 nada é silenciado e a limitação é declarada.

---

## 13. Integração Personal Trainer / LLM

**Entregue na F3.2.** `intelligence_package`: `{question?, window, timezone, package_version, metrics (achatadas do overview), baselines[], evidence[] com `insight_code`/`severity`/data, context_events[], muscle_groups[], data_quality, data_quality_overall, limitations[], allowed_actions[], truncated, size_bytes}` (≤ 40 KB, truncamento **declarado**, nunca silencioso). `insights_core.dispatch_insights` monta o contexto server-side a partir do pacote (frontend envia só `question` + `window`); RAG filtrado por grupos presentes (`rag_chunking.select_rag_text`) em vez do dump inteiro; prompt versionado em `app/insights_prompt.py` com as regras "não recalcular, não inventar, não causalidade" mais "citar evidência pela data" e o vocabulário fechado de ações; `insights_ai.py` deixou de guardar a chave numa constante de módulo e passou a usar `openai_client.call_openai`.

`muscle_groups[]` não é enfeite: são os NOMES que filtram o RAG. Se a lista esvaziar, o prompt volta ao dump completo — por isso ela é a **última** da ordem de truncamento.

**`evidence[].observed_scope`** (`point` | `window`), com `observed_from` no segundo caso. "A data da evidência" não é uma coisa só: **12 das 26** chamadas de `insight_rules._evidence` não informam `observed_at`, e não informam de propósito — são agregados de JANELA (`weekly_effective_sets[grupo]`, `working_sets_total`, `progress_share`) ou constantes de PLANO (`target_min_weekly_sets`), que têm um período e não um dia. O pacote completa essas com a janela e **marca o alcance**; o prompt tem uma forma de citação para cada (`(dd/mm/aaaa)` vs `(janela dd/mm a dd/mm)`). As três alternativas foram descartadas com motivo: repassar `None` entrega ao modelo uma evidência que o próprio prompt o proíbe de citar; descartar tiraria a evidência central do alerta mais comum do produto; e inventar um dia único para um agregado de 28 dias seria falso com aparência de precisão.

PT externo consome `GET /analytics/dashboard/intelligence-package`. **A rota não aceita `user_id`**: o dono é sempre a identidade da requisição, o que torna o escopo estrutural em vez de uma checagem que se pode esquecer. O token dedicado do PT (escopo de leitura, revogável, sem escrita) está **proposto e não entregue** — ver o parágrafo da F3.2 em §17 para por que reusar `devices` como está daria identidade admin ao PT, e qual é o caminho (uma migração própria: `devices.user_id` + `devices.scope` — o número 055 já foi ocupado pela Fase 4, ver §17).

---

## 14. Migrations e retrocompatibilidade (todas aditivas, com `down`)

- `044_users_timezone.py` — `users.timezone VARCHAR(64) NOT NULL DEFAULT 'America/Sao_Paulo'`.
- `045_equipment_exercises_load_convention.py` — `ENUM('total','per_side','per_dumbbell','assisted','bodyweight','bodyweight_plus') NULL`; leitura trata NULL como `total`; sem backfill.
- `046_body_measurements.py` — `id, user_id, local_date, site ENUM('waist','hip','chest','arm_l','arm_r','thigh_l','thigh_r','calf_l','calf_r'), value_cm DECIMAL(5,1), protocol_ok TINYINT(1) DEFAULT 1, notes, source, created_at`; UNIQUE `(user_id, local_date, site)`.
- `048_context_events.py` (**entregue na F2.1**; o plano previa 047, mas a 046 foi ocupada pela reversão da convenção de carga e a 047 por `body_measurements` — a numeração desloca daqui para frente) — `id, user_id, event_type ENUM('alcohol','travel','illness','atypical_sleep','gym_change','plan_change','diet_phase','supplement','injury_pain','other'), local_date, end_local_date NULL, intensity TINYINT NULL, notes, source ENUM('manual','derived'), created_at, updated_at`; índice `(user_id, local_date)`; **sem UNIQUE** (dois eventos do mesmo tipo no mesmo dia são legítimos → POST é INSERT, não upsert). `updated_at` acrescentado ao DDL do plano porque o CRUD tem PUT. Evento `derived` **não é materializado**: troca de ficha e de academia são derivadas na LEITURA (`intelligence/dashboard/context_service.py`) de `workout_plan_template_assignments` e `activities.gym_id` — idempotência por construção e sem dupla verdade.
- `049_template_assignment_history.py` (**entregue no M23**, fora da sequência do Novo Dashboard; o plano reservava a 049 para `daily_checkins`, mas o M23 chegou antes e a F2.2 desloca para 050/051) — `workout_plan_template_assignments` ganha `unassigned_at DATETIME NULL` (NULL = vigente), `unassigned_by_user_id BIGINT NULL` e a coluna gerada `active_user_id BIGINT GENERATED ALWAYS AS (IF(unassigned_at IS NULL, user_id, NULL)) VIRTUAL` com `UNIQUE (active_user_id)`, que torna **uma atribuição vigente por usuário** uma invariante do BANCO; sai o `UNIQUE (template_id, user_id)` (ele proibiria voltar a um Modelo já largado) e entra `KEY idx_wpta_template (template_id)` para apoiar a FK depois do DROP. Backfill aditivo: a atribuição mais recente de cada usuário fica vigente, as anteriores recebem `unassigned_at` = `assigned_at` da seguinte. Desatribuir deixa de ser DELETE físico. **Isto resolve a linha "histórico de `assignments`" que estava em "Fora do MVP" (M15) e fecha a lacuna "troca de template não deixa rastro" do §4.**
- `050_daily_checkins.py` (**entregue na F2.2**) — `id, user_id, local_date, energy/soreness/stress/mood TINYINT NULL (1..5), notes, created_at, updated_at`; UNIQUE `(user_id, local_date)` → o POST é **upsert** ("o check-in de hoje" é único por definição; repetir CORRIGE). Desvios do DDL do plano, ambos deliberados: os quatro escores nascem **NULL** (check-in parcial é legítimo; `null != 0`, e escore ausente não entra na baseline) e **não existe `sleep_quality`** — duração e regularidade do sono já vêm de `sleep_metrics`/`intervals_wellness` (objetivo, medido), e um escore subjetivo de sono seria uma segunda verdade sobre a mesma coisa. `soreness` é a dor GLOBAL do dia (inclusive em dia sem treino) e **não** se soma a `exercises.muscle_pain`, que é por sessão.
- `051_dashboard_alert_states.py` (**entregue na F2.2**) — `id, user_id, alert_key VARCHAR(500), alert_key_hash CHAR(64), rule_code, fingerprint VARCHAR(500), severity ENUM('INFO','ATTENTION','ACTION'), last_reason VARCHAR(32), first_seen_at, last_seen_at, last_emitted_at, emitted_count, suppressed_count, resolved_at NULL, snoozed_until NULL, created_at, updated_at`; UNIQUE `(user_id, alert_key_hash)`, KEY `(user_id, rule_code)`. Cinco desvios do DDL do §12, todos medidos no dataset real de DEV (usuário 1, 8 leituras de `/dashboard/insights` entre 2026-08-14 e 2026-09-25): (1) a identidade é `alert_key` (código + as chaves do **alvo**, declaradas por código em `insight_rules.COOLDOWN_KEYS_BY_CODE`) e **não** a `fingerprint` — `EXERCISES_REGRESSING` produziu 4 impressões nas 8 leituras (a lista `exercise_ids` muda sozinha) e `RECOMPOSITION_COMPATIBLE`/`DATA_QUALITY_UNMAPPED_SETS` levam `date_to` na chave (impressão nova todo dia), então cooldown por impressão nunca silenciaria nenhuma das três; (2) `alert_key_hash` porque a impressão não tem teto de tamanho (95 caracteres hoje, e cresce com a lista) e truncar dentro de um UNIQUE é colidir — colisão aqui silencia para sempre um alerta que não é o mesmo; (3) `last_emitted_at`/`emitted_count`/`suppressed_count` separam "vista" de "MOSTRADA" — sem isso não se responde "quantas vezes este alerta foi silenciado?", que é a pergunta que diz se o limiar está calibrado; (4) `severity` para a escalada poder furar o cooldown; (5) `last_reason` para a segunda leitura do MESMO dia devolver a mesma resposta (ver §12). Sem DML e sem backfill: não existe histórico de "alerta mostrado", e inventar um silenciaria alerta que o usuário nunca viu.
- `054_generation_runs.py` (**entregue no M16**, pré-requisito da F3.1; as 052/053 ficaram com o M12/M13) — `id, user_id, workout_plan_id NULL, job_id VARCHAR(36) NULL, template_id NULL, template_slot_sequence NULL, volume_mode VARCHAR(40), engine_version VARCHAR(40), llm_used TINYINT(1) NOT NULL DEFAULT 0, plan_date DATE NULL, audit_hash CHAR(64), audit_json JSON NOT NULL, request_json JSON NULL, created_at`; KEYs `(user_id, created_at)`, `(workout_plan_id)`, `(user_id, audit_hash)`, `(template_id, created_at)`. Quatro decisões: (1) o `generation_audit` vai **inteiro num JSON** e só sobem a coluna os campos pelos quais a F3.1 filtra ou junta — a forma do bloco difere entre Modo A e Modo B e ainda muda com os motores, então normalizá-la em colunas congelaria essa forma em DDL e criaria uma segunda verdade ao lado do bloco que continua no `raw_payload`; contagens derivadas (fallbacks, substituições) **não** viram coluna pelo mesmo motivo. (2) A ligação rascunho → ficha é o `audit_hash` (sha256 do JSON canônico do bloco) e **não** um `generation_run_id` carimbado dentro do bloco: a ficha nasce num POST separado da geração, e carimbar mudaria o formato do `generation_audit` — que este PR deliberadamente não toca. O hash **não** é UNIQUE (duas gerações de auditoria idêntica não podem fazer a segunda FALHAR); a ligação marca a linha mais antiga ainda sem ficha. (3) `workout_plan_id` com FK física `ON DELETE SET NULL` (apagar a ficha não desfaz o fato de que a geração aconteceu) e `template_id` com FK **lógica** (FK física apagaria o agrupamento histórico do Modelo). (4) Sem coluna `status`: a linha só nasce quando um rascunho FOI produzido. `request_json` guarda o payload do job do DynamoDB, que é a única parte dele que morre no TTL de 6h. Sem DML: backfill inventaria a data da GERAÇÃO (só existe a da ficha) e não teria `request_json`. **Duplicação declarada**: `raw_payload.generation_audit` continua sendo gravado (o congelamento do Modo B o lê) até a F3.1 estar em produção lendo `generation_runs`.
- `055_muscle_groups_porte_e_agregacao.py` (**Fase 4, PR B** — fora da sequência do Novo Dashboard; entrada aditiva, nada aqui muda) — `muscle_groups` ganha `size_class ENUM('large','small','abs','unclassified') NOT NULL DEFAULT 'unclassified'` e `methodology_parent_group_id BIGINT NULL` (self-FK `ON DELETE SET NULL`, `KEY idx_mg_methodology_parent`). São o vocabulário da metodologia de volume por grupo (porte do grupo; e a fatia que soma no bloco, ex. `Peitoral superior` → `Peitoral`), semeados por nome a partir dos nomes que a `002_reestruturacao.sql` fixou e editáveis por admin em `/dominio-exercicios`. **Nenhuma leitura do painel consome estas colunas** — o motor e os endpoints vêm nos PRs seguintes daquela fase.

Padrão: um `tests/unit/test_migration_0XX_plano_aurora.py` por migração (recorte `aurora_schema_*.json`), rodada pelo workflow `db-migrations` existente.

---

## 15. Performance, segurança e observabilidade

- Helper `require_user_id()` em `intelligence/dashboard/`; nenhum serviço novo aceita `None`. Corrigir `DashboardAggregatorService` (propagar `user_id`) como pré-requisito.
- Validação de filtros via Pydantic (`window`, datas ≤ 365 dias, ids inteiros); IDOR: todo id de exercício/plano filtrado por `user_id` na query.
- Evitar N+1: `exposure_service` carrega histórico de um exercício em 1 query; `overview` compartilha `get_muscle_group_overview` entre blocos; respeitar 1 MB da Data API (limitar `exposures` a 30, paginar `workouts`).
- Índices a avaliar após EXPLAIN em dev: `exercises(mapping_id, equipment_id)`, `exercise_sets(exercise_id, set_type)`, `body_measurements(user_id, site, local_date)`.
- Logs: `dashboard.computed`, `insight.emitted`, `dq.snapshot` via `observability.log_event`; métricas CloudWatch por log filter (latência, erro).
- Dados pessoais: sem fotos corporais no MVP; `body_measurements` só com `user_id` do próprio.

---

## 16. Estratégia de testes

- Unit (`api/tests/unit/`): `test_dashboard_comparability.py` (§16.1 do brief: mesma carga+reps, carga↑ reps↓, RPE menor, máquina diferente, alias mantém histórico, assistido invertido, halter/por lado, RPE ausente ≠ 0), `test_dashboard_effective_sets.py` (aquecimento fora; planejado não entra; 3×1,0 + 4×0,4 = 4,6; direto/indireto separados; projetado não contamina), `test_dashboard_adherence.py` (ciclo 8 dias; Upper/Lower sem branch; pulada não desloca exposição; troca de ficha = fronteira), `test_recovery_signals.py` (HRV isolado fraco; HRV+FC+desempenho eleva; álcool = associação; sinais divergentes; dor bloqueia intensificação; wearable ausente não zera), `test_body_service.py` (recomposição; bioimpedância isolada; média móvel por timezone; fora de protocolo → LOW), `test_data_quality_service.py`, `test_insight_rules.py` (cooldown/fingerprint), `test_dashboard_contracts.py` (null ≠ 0, envelope, versões), `test_migration_044..051`, `test_paridade_rotas_engines.py` (já cobre rotas novas).
- Integration (MySQL): ownership A/B em todos os endpoints novos; importação repetida idempotente; classificação canônica alterada reflete em `muscles` e `exercises`; payload < 1 MB com seed de produção.
- Frontend (vitest): `dashboard-filters`, `dashboard-format`, `status-labels`, fetchers; smoke `npm run smoke:export`.
- Manual em DEV (regra de memória): após merge, atualizar ambiente, rodar migrações/seed e só então pedir validação.

---

## 17. Roadmap em fases (cada item = 1 PR contra `develop`, delegado a `fullstack`/`aws`, base PR-a-PR)

**Fundação (F0)**
- F0.1 `contracts.py` + `metrics_registry.py` + `time_windows.py` + `require_user_id` + correção de `DashboardAggregatorService`; migração 044 (timezone) + `PATCH /users/me`. Testes de contrato. Aceite: `response_model` nas rotas novas, `null` p/ ausente. Rollback: rotas novas não usadas.
- F0.2 Migração 045 (`load_convention`) + UI de cadastro em `/equipamentos-exercicios` (campo por par) + `comparability.py` + `exposure_service.py` + `GET /analytics/dashboard/exercises/{id}/progress` (+ `api_gateway.tf`/nginx). Aceite: testes §16.1 verdes.
- F0.3 `data_quality_service` + `GET /analytics/dashboard/data-quality` + tela `/dashboard/data-quality`. Aceite: itens com `fix_link`.
- F0.4 Frontend base: `dashboard-api.ts`, `StatusBadge` único, `MetricValue`, skeleton/empty/partial, `staleTime`, filtro global estendido, visão geral passa a usar o filtro. Aceite: 5 cópias de rótulos removidas.

**MVP (F1)**
- F1.1 `adherence_service` + `GET /analytics/dashboard/workouts` + `/dashboard/treinos` reescrita (itens concluído/pulado/substituído/interrompido).
- F1.2 `muscle_summary_service` + `GET /analytics/dashboard/muscles`, `/muscles/{id}` + `/dashboard/musculos` (realizado/planejado/projetado separados, direto/indireto, frequência, drill-down até série).
- F1.3 `/dashboard/exercicios/[id]` (usa F0.2) + progressão por grupo (reusa `dashboard-progression-by-muscle-group`).
- F1.4 Migração 046 + CRUD `body_measurements` + `body_service` + `GET /analytics/dashboard/body` + `/dashboard/corpo` (absorve `/evolucao-corporal` e bioimpedância de `/indicadores-fisicos`).
- F1.5 `recovery_signals_service` + `GET /analytics/dashboard/recovery` (envelope) + `/dashboard/recovery` com painel de sinais (absorve `/recuperacao-muscular`).
- F1.6 `insight_rules` v1 + `overview_service` + `GET /analytics/dashboard/overview`, `/insights` + `/dashboard` com 6 blocos e "Próxima ação". Feature flag `NEXT_PUBLIC_DASHBOARD_V3` para alternar visão geral antiga/nova até aceite.
- F1.7 Mobile: revisão iPhone de todas as telas do hub (Drawer de filtros, cards, safe-area).

**Fase 2**
- F2.1 Migração **048** + CRUD `context_events` (api-core) + derivados na leitura (troca de ficha/template/academia) + `GET /analytics/dashboard/context` + `/dashboard/contexto` timeline + associações em recovery/insights (`PERFORMANCE_DROP_WITH_CONTEXT_EVENT` ligada) + insight próprio `TRAINING_PAUSE_DETECTED` ("você parou de treinar"), que é de CONTEXTO e não da regra de volume — esta silencia de propósito numa pausa total (decisão do usuário, 2026-09-24).
- F2.2 **(entregue)** Migração **050** `daily_checkins` + **051** `dashboard_alert_states` (a 049 foi ocupada pelo M23, histórico de atribuição de Modelo): CRUD `/daily-checkins` (api-core) + tela `/dashboard/checkin` + os quatro escores como sinais subjetivos em `recovery_signals` (contando UMA vez na concordância) + **cooldown real** de insight em `overview_service` e `/insights` (`?include_suppressed`). Severidade `INFO/ATTENTION/ACTION` já é a do contrato novo desde a F1.6 (`contracts.Severity`, `insight_rules`); o `DashboardAlertService` v2 (`/analytics/dashboard-alerts`, `INFO/WARNING/CRITICAL`) **não foi tocado** — ele serve as telas antigas que a F2.4 aposenta, e mapear severidade nele criaria uma segunda tabela de severidade para morrer duas etapas depois (fica como pendência da F2.4, não como dívida da F2.2).
- F2.3 **(entregue)** Cardio no hub: `GET /analytics/dashboard/cardio` + tela `/dashboard/cardio`, sem migração nova (a tabela já existia — `activity_intervals_link`, migração 041/M2). Três desvios do enunciado, todos deliberados: (1) a fonte NÃO é `/integrations/intervals/*` nem `cardio-agent-context`, e sim o CACHE `intervals_activities` lido direto na Lambda `engines` — a rota antiga consulta o Intervals.icu AO VIVO (precisa da chave do usuário, que a `engines` não tem e não deve ter, e depende da API externa estar de pé), e o `cardio-agent-context` é um agregador de 8 blocos montado para o prompt do agente, não um contrato de tela; em troca a resposta DECLARA a data da última sincronização (`last_synced_at`), porque dado velho declarado é melhor que dado novo instável. (2) A associação FC ↔ sessão é LIDA de `activity_intervals_link`, nunca refeita: refazer o casamento por horário na leitura criaria uma segunda verdade, que divergiria da do job assim que a janela de tolerância mudasse — e o `match_status` sobe ao payload, para `MATCHED_AMBIGUOUS` aparecer como dúvida e não como certeza. (3) A heurística de classificação cardio/força/outra saiu de `intervals_integration.py` para o módulo puro `app/intervals_activity_kind.py`, importado pelos dois lados: duas cópias fariam `/cardio` e `/dashboard/cardio` discordarem sobre o que foi cardio. Quatro estados, quatro limitações (sem Intervals configurado, configurado sem sincronização, sincronizado sem cardio na janela, cardio sem FC) — uma frase genérica mentiria em três deles.
  **M98 C4 (2026-10-27) — acompanhamento do cardio.** `/dashboard/cardio` ganhou minutos por zona Z1–Z5 e contínuo × intensa por semana, planejado × realizado (aderência do dono `cardio_adherence`), CTL/ATL/TSB, HRV, FC de repouso, PAI, a próxima sessão da última recomendação `cardio-agent-v3` e as zonas em uso com a origem — tudo pronto no backend (mapa §69.6). A tela antiga `/cardio` e o endpoint AO VIVO `/integrations/intervals/cardio-dashboard` saíram (`/cardio` → 301 para `/dashboard/cardio`): o Intervals passa a ser lido só pelo cache, e a heurística de classificação existe só em `intervals_activity_kind` (guarda `cardio_paralelo` zerada).
- F2.5 (frente A) **(entregue)** **Paridade de `/dashboard/recovery`**, pré-requisito da aposentadoria de `/recuperacao-muscular`. **M33**: mapa corporal, recomendação de séries, fatores do cálculo e o ajuste global de fadiga (TSB) passam a sair de `GET /analytics/dashboard/recovery-signals`, **repassados** do mesmo `MuscleRecoveryService.get_recovery_overview` — nenhum número é recalculado e nenhuma consulta nova foi aberta (`_get_global_tsb` ganhou memo de instância, então o ajuste global vem da mesma leitura que o overview já fez). Com isso o aviso que mandava o usuário de volta para a tela legada **saiu da tela**. **M36**: o recorte "dia com treino × dia sem treino" (sono, HRV, FC de repouso) sai de `/treinos` (onde estava sob o rótulo de cardio) e nasce em `recovery_signals` como bloco `training_vs_rest`, a partir das linhas que a tela já lia — descritivo, fora de `signals`, sem entrar em `level` nem em contagem de concordância; "dia de treino" é o dia com sessão registrada no app, e não mais o dia em que uma atividade WeightTraining do Intervals.icu casou. **M32**: o teste de paridade de payload de `/dashboard/muscles` com Modelo passou a congelar o relógio (`congelar_relogio`), que era a causa do flake de 0,01 h em `hours_since_last_stimulus`.
- F2.5 (frente B) **(entregue — M34+M35)** Paridade final de `/volume-grupamentos` e `/exercicios`, o que destravou as duas linhas "FICA" da tabela da F2.4. `GET /analytics/dashboard/muscles?include_comparatives=true` passa a devolver o bloco `comparatives`: comparativo das **últimas SESSÕES** (T-0..T-3), comparativo por **EQUIPAMENTO** e **mapa de calor**; `/dashboard/exercicios/[id]` ganha o **RPE médio por sessão** (cartão com a linha em escala fixa 0–10 mais as colunas "Melhor carga" e "RPE médio" no histórico). Três desvios deliberados da tela legada: (1) **a unidade é declarada** — `unit: "session"` e cabeçalho próprio, porque todo o resto de `/muscles` compara por JANELA; a `/volume-grupamentos` empilhava quatro tabelas (duas por sessão, duas por semana) com os mesmos nomes de coluna, e não havia como saber se "caiu de T-1 para T-0" era o treino passado ou a semana passada. (2) **Séries preparatórias ficam fora, inclusive do RPE** (`sql_not_preparatory`): RPE de aquecimento mede o esforço de aquecer, e como a progressão do projeto já lê só `working`, incluí-lo aqui criaria dois RPE médios incompatíveis na mesma interface. (3) **Mapa de calor com faixa DISCRETA classificada no backend** (`classify_heatmap_band`, §11) e grade de células em vez do treemap com gradiente interpolado no cliente: cada célula traz nome, valor, rótulo da faixa e `aria-label`, então cor deixa de ser o único sinal (§14) e nada colapsa a ponto de sumir num celular. O grupo do estímulo sai das **contribuições ponderadas**, não de um "grupo primário" único por exercício como na tela legada. Custo: **UM statement**, opt-in — os três blocos são recortes da MESMA agregação (`grupo × sessão × equipamento`), e a igualdade `sem bloco + 1` é travada por teste nos dois datasets de medição.
- F2.4 **(ENCERRADA em 2026-09-26 por decisão do usuário — 2 de 6 telas aposentadas; ver a 2ª passada no fim desta seção)** Aposentar telas antigas. A checagem de paridade tela a tela mostrou que o hub v3 **ainda não cobre 5 das 6**, e aposentadoria prematura tira função de quem usa o app todo dia. Só saiu o que tinha sucessor completo:

  | Tela antiga | O que mostra | Onde isso existe hoje no hub v3 | Veredito |
  |---|---|---|---|
  | `/evolucao-corporal` | peso, massa muscular, gordura e água no tempo (2 gráficos) + tabela da balança (peso, massa, gordura, água, visceral, IMC) | os **mesmos dois gráficos** e uma tabela **com mais colunas** (IMC, visceral, subcutânea, óssea, TMB, proteínas, idade metabólica) já estão em `/indicadores-fisicos`; a leitura analítica (média móvel de 7d, recomposição, alinhamento com e1RM) está em `/dashboard/corpo` (F1.4) | **APOSENTADA** → 301 para `/dashboard/corpo` |
  | `/recuperacao-muscular` | estado de recuperação por grupo + **mapa corporal**, recomendação de séries e fatores do cálculo | era o bloqueio desta fase: `/dashboard/recovery` tinha sinais objetivos + prontidão por grupo, mas **não tinha** mapa corporal, recomendação de séries nem os fatores — a própria tela v3 dizia isso num alerta e **linkava de volta** para a antiga. A **F2.5-A (M33)** fechou os três, e o alerta saiu | **FICAVA** → **desbloqueada pela F2.5-A**; o redirect é decisão da próxima passada |
  | `/treinos` | resumo semanal, semana ISO, RPE, **Cardio & Recuperação (Intervals: FC, duração, sono e HRV em dia de treino × dia de descanso)**, **análise com personal trainer IA** (`POST /workouts-analysis/generate`), ativação muscular 7d | `/dashboard/treinos` cobre consistência/aderência e sessões. A F2.3 **(entregue)** fechou parte do bloco de cardio — `/dashboard/cardio` cobre **FC média por treino casado** e **duração média das sessões casadas**. O **M36** (sono e HRV em dia de treino × dia de descanso) foi entregue pela **F2.5-A**, em `/dashboard/recovery` — é recuperação, não cardio. ~~Continua sem equivalente a **análise por IA**~~ — a **F3.2** entregou a análise por IA sobre o `intelligence_package` (`POST /insights/generate` server-side), que é o mesmo tipo de resposta que `/treinos` pedia, agora com evidência citada por data e sem recálculo | **APOSENTÁVEL** (aguardando o redirect na próxima passada da F2.4) |
  | `/volume-grupamentos` | comparativo por SESSÃO (T-0..T-3), comparativo semanal, comparativo por EQUIPAMENTO, **mapa de calor** (treemap) | ~~`/dashboard/musculos` é por JANELA e não tem recorte por sessão, por equipamento nem mapa de calor~~ — **a F2.5/M34 entregou os três** em `/dashboard/musculos`, num bloco com unidade declarada (`unit: "session"`). O comparativo SEMANAL não foi portado de propósito: ele é a leitura por janela que a própria tela já tem, com outro nome | **APOSENTÁVEL** (aguardando o redirect na próxima passada da F2.4) |
  | `/exercicios` | busca por nome → melhor carga/volume por sessão e **RPE médio por sessão** | `/dashboard/progressao` tem seletor buscável de exercício e leva a `/dashboard/exercicios/[id]`; ~~não há RPE agregado por sessão~~ — **a F2.5/M35 o entregou** (cartão de RPE médio por sessão + colunas de carga e RPE no histórico), fechando carga, volume e RPE por sessão | **APOSENTÁVEL** (aguardando o redirect na próxima passada da F2.4) |
  | `/insights` | **pergunta livre à IA** com contexto analítico (`POST /insights/generate`) | `/dashboard/insights` é outra coisa: lista de REGRAS disparadas (`insight_rules`) com evidência, cooldown e selos Novo/Voltou/Piorou/Já visto (F2.2). Não responde pergunta livre — ~~isso é a F3.2~~ **a F3.2 entregou**: a pergunta livre continua no MESMO endpoint (`POST /insights/generate`), mas o contexto passou a ser o `intelligence_package` montado no servidor e o RAG passou a ser filtrado pelos grupos da janela. A tela `/insights` perdeu o textarea de "contexto analítico" e manda só pergunta + janela | **APOSENTÁVEL** — o substituto existe; falta decidir onde a pergunta livre mora no hub (`/dashboard/insights` é a lista de regras; a pergunta livre pede uma caixa própria) |

  Backend: as rotas `/analytics/muscle-groups*` de regex **já estavam** em `legacy_endpoint_telemetry` (PR #87). Das `dashboard-*` v2, entraram **só duas**: `dashboard-overview` (→ `/analytics/dashboard/overview`) e `dashboard-alerts` (→ `/analytics/dashboard/insights`), as únicas sem nenhum consumidor no frontend. As outras onze (`muscle-status`, `muscle-drilldown`, `progression*`, `workout-sessions`, `recovery`, `cycle`, `engine-audit`, `stimulus-fatigue`) **não entraram**: são a implementação ATUAL por trás de telas vivas do hub v3 — marcá-las com `Deprecation` seria mentira. Nenhum endpoint removido: medir primeiro.

  Três itens que a fase carregava:
  - **`template_type`** — **fica**. A premissa era que a coluna cairia junto com as rotas legadas que leem `workout_plans.plan_type`, mas nenhuma delas é aposentada aqui, e `plan_type` está vivo em caminho quente: `services.py:2100` bifurca a lógica de progressão em `plan_type == 'abc'`, `intelligence/volume_service.py:2359` atribui ciclo ABC com `WHERE wp.plan_type='abc'`, `training_load_service.py:79` o lê, `workout_plan_equipment.py:74` também, e `generation/mode_a.py:847` (e mode_b/legacy) **derivam** `plan_type` de `template_type`. Derrubar a coluna agora quebraria geração de ficha. Sai quando `abc_workout_template.py`/`abc_workout_ai.py` saírem.
  - **Severidade do serviço v2** — **continua deliberadamente não mapeada**. `DashboardAlertService` (`INFO/WARNING/CRITICAL`) tem um único ponto de entrada, `GET /analytics/dashboard-alerts`, e ele **não tem consumidor**. A rota não foi removida (telemetria primeiro), então a pendência não morreu nesta fase — ela morre no PR que remover a rota, depois de >= 7 dias sem evento em produção.
  - **M31** (`get_consecutive_day_recovery_checks`, leitura série a série antiga) — **sobrevive**. Único consumidor: `dashboard_modules.py:72` → `GET /analytics/muscle-recovery-consecutive-check` → tela `/treinos`, que **não** foi aposentada.

  Dependência da F2.3, reavaliada depois que ela entrou (PR #229): a F2.3 cobriu **FC e duração** das sessões casadas, mas **não** o recorte "sono e HRV em dia de treino × dia de descanso" — que é justamente o que a seção "Cardio & Recuperação" de `/treinos` usa para responder "treinar está me custando sono?". Isso virou o **M36**. Ou seja: a F2.3 **reduziu** o bloqueio de `/treinos` de dois para dois de natureza diferente — M36 e F3.2 —, mas **não o removeu**. `/treinos` continua sem poder ser aposentada.

  **Reavaliação depois da F2.5-A (frente A, M33+M36):** duas linhas da tabela acima mudaram de estado. `/recuperacao-muscular` **deixou de ter bloqueio**: mapa corporal, recomendação de séries, fatores do cálculo e o ajuste global de fadiga passaram a sair de `/dashboard/recovery`, e o alerta que linkava de volta saiu da tela — a aposentadoria dela é agora só uma decisão, não uma pendência técnica. `/treinos` **perdeu um dos dois bloqueios** (o M36 entregue); sobra a **análise por IA** (→ F3.2), então ela continua sem poder ser aposentada.

  **Reavaliação depois da F3.2:** as duas últimas linhas "FICA" caíram. `/treinos` perdeu o bloqueio restante — a análise por IA passou a existir sobre o `intelligence_package`, com evidência citada por data e sem recálculo. `/insights` ganhou substituto funcional no mesmo endpoint (contexto montado no servidor, RAG filtrado); o que sobra é **decisão de produto**, não pendência técnica: onde a caixa de pergunta livre mora no hub, já que `/dashboard/insights` é a lista de regras disparadas. Com isso, **nenhuma das seis telas da tabela tem bloqueio técnico** — a próxima passada da F2.4 é uma sequência de redirects.

  **Correção da conclusão acima, pela 2ª passada da F2.4 (2026-09-26):** o `intelligence_package` está pronto, mas `/dashboard/insights` **não tem caixa de pergunta livre** — `DashboardInsightsClient.tsx` lista regras disparadas com paginação e filtro de suprimidos, e não chama `POST /insights/generate`. Enquanto a caixa não existir, `/insights` e a "análise com PT IA" de `/treinos` continuam **sem substituto de UI**, e as duas telas não podem ser aposentadas. A lacuna é de interface, não de backend — mas lacuna de interface também tira função de quem usa o app. A frase "nenhuma das seis telas tem bloqueio técnico" vale para o servidor e **não** para a tela.
  ---

  ### F2.4 — 2ª passada (2026-09-26): reverificação tela a tela — **FASE ENCERRADA**

  > **Decisão do usuário, 2026-09-26: "pare a paridade".** A F2.4 está **concluída** com **2 de 6 telas aposentadas** (`/evolucao-corporal` na 1ª passada, `/recuperacao-muscular` nesta). As quatro restantes — `/volume-grupamentos`, `/exercicios`, `/insights` e `/treinos` — **ficam por decisão, não por pendência**: continuam no menu, sem redirect, e não há trabalho de paridade pendente sobre elas.
  >
  > A tabela abaixo permanece como **inventário**, não como backlog. As lacunas listadas **não viram item M** — elas existem para que, se um dia uma tela específica incomodar, já se saiba exatamente o que falta e onde, sem refazer a leitura. Perseguir paridade de tela que ninguém pediu para aposentar é custo sem dono.
  >
  > Por consequência, o **M31** (`get_consecutive_day_recovery_checks` + `GET /analytics/muscle-recovery-consecutive-check`) fica vivo enquanto `/treinos` viver. Isso não é dívida: é o preço de manter uma tela que a decisão manteve de propósito.

  A tabela abaixo foi escrita a partir do que cada M **prometeu** entregar. Esta passada reabriu as cinco telas restantes e conferiu **por leitura de código, bloco a bloco e coluna a coluna**, a tela legada contra a(s) do hub. Duas linhas que estavam marcadas "APOSENTÁVEL" **não passaram**: o M34 e o M35 entregaram o que prometeram, mas as telas legadas mostram MAIS do que a lacuna registrada no backlog. Promessa de M não é inventário de tela — e é essa diferença que esta passada mediu.

  | Tela antiga | Veredito | O que decidiu |
  |---|---|---|
  | `/recuperacao-muscular` | **APOSENTADA** → 301 para `/dashboard/recovery` | Cobertura completa depois do M33. Última pendência era a coluna **"RPE médio"** (o da JANELA), que já vinha no payload como `factors.avg_rpe_window` desde o M33 mas **nenhuma tela exibia** — passou a aparecer nos "Fatores", com rótulo próprio para não se confundir com o `avg_rpe_used` (o que entrou no cálculo da dose). "Bom para" é `agora + horas restantes`, já apresentado como a coluna "Falta"; `status`/`guidance`/`readiness_status` são o MESMO estado (`_STATUS_META`), e a tela antiga exibia as três leituras lado a lado. O hub ainda ACRESCENTA sinais objetivos contra baseline e o recorte treino × descanso (M36) |
  | `/volume-grupamentos` | **FICA** (3 lacunas) | O M34 entregou os três blocos POR SESSÃO, mas a tela legada tem outros: **(1) aba "Tendência"** — volume em **kg** e **repetições** por grupo, janela atual × anterior (`/analytics/training-volume`); `/dashboard/musculos` mede em **séries efetivas/semana** e não tem nem kg nem reps por grupo em nenhuma janela; **(2) "Peso na última série"** por grupo × equipamento × semana — o `by_equipment` do hub traz séries, tonelagem e RPE, mas não a carga da última série; **(3) mapa de calor por SEMANA e por MÊS** — o do hub é só por sessão. (O comparativo semanal T-x continua fora de propósito, como já estava documentado.) |
  | `/exercicios` | **FICA** (2 lacunas) | O M35 fechou o RPE por sessão, mas: **(1) profundidade de histórico** — a tela legada consulta `days=1095` **sem teto de linhas**, enquanto `/dashboard/exercicios/[id]` tem `MAX_EXPOSURES = 30` e janela máxima de 365 dias (`MAX_CUSTOM_WINDOW_DAYS`); para um exercício de três anos o hub mostra um pedaço; **(2) nome da atividade** — a coluna "Atividade" (`a.name`) não existe em `Exposure`. O "RPE alto" não é lacuna: o hub mostra o RPE de CADA série, do qual a contagem é derivável |
  | `/insights` | **FICA** (1 bloqueio, de UI) | O #241 (F3.2) mudou o **backend** (`intelligence_package`, contexto montado no servidor), mas `/dashboard/insights` **não tem caixa de pergunta**: ele lista regras disparadas com paginação e o filtro de suprimidos, e nada mais. Sem interface de pergunta livre não há substituto, por mais pronto que o backend esteja. A caixa é escopo próprio, não desta passada |
  | `/treinos` | **FICA** (2 bloqueios) | Além da **análise por IA** (mesma pendência de UI da `/insights`), a tela tem as tabelas semanais de **volume em kg**, **séries de RPE alto** e volume por grupo — a mesma lacuna de unidade que segura a `/volume-grupamentos`. O **M37 foi entregue** nesta passada (ver abaixo), mas ele nunca foi bloqueio de aposentadoria: é uma leitura que faltava no hub |

  **M37 — entregue aqui.** `GET /analytics/dashboard/cardio` passou a devolver `unmatched_strength_activities_total`: atividades `WeightTraining` do Intervals.icu na janela que **não** estão em `activity_intervals_link`. É o lado do INTERVALS do casamento — `strength_heart_rate` conta sessões sem FC, este conta atividades que sobraram do outro lado (treino não registrado no app, ou horário fora da tolerância de 60 min). **Um statement**, e ele só é emitido quando a janela tem atividade de força vinda do Intervals: sem ela a diferença é vazia por construção. A classificação cardio/força continua em Python (a consulta traz só ids), pela mesma razão do resto do módulo. Aparece como selo no bloco de FC e como ressalva — só quando há sobra.

  **Telemetria: nenhuma rota entrou.** Aposentar `/recuperacao-muscular` não deixou nenhum endpoint sem consumidor. `GET /analytics/muscle-recovery`, que a tela consumia, **continua vivo no servidor**: `intelligence/template_volume_planner.py:81` o chama por `_internal_get` no caminho sem `user_id`, e `engines_client.py` o lê no mesmo request de CRUD. Marcá-lo com `Deprecation` seria mentira — a mesma regra que manteve as onze `dashboard-*` fora da 1ª passada.

  **Fora da F2.4 (2026-10-01): `/grupos-musculares` APOSENTADA → 301 para `/dashboard/musculos`.** Decisão do usuário, depois que o #292 levou a "Tendência por grupo muscular" (filtros, secundários e ranking) para `/dashboard/musculos`. A edição do catálogo canônico já existia em `/dominio-exercicios` (abas Exercícios e Mapeamentos externos) e "Exercícios sem classificação" na aba "Sem classificação" do mesmo domínio. **Descartados sem substituto, por decisão:** KPIs de cobertura, "Tabela completa por grupo muscular" e o "Catálogo de mapeamento canônico" com "Exportar Markdown". Mesmo padrão das anteriores: 301 na CloudFront Function + `LegacyPathRedirect` de fallback; item sai do menu. **Nenhum endpoint removido**; ficaram sem consumidor no frontend (candidatos a limpeza futura, medir antes): `GET /analytics/muscle-groups-mapped-table`, `GET /exercise-mapping-catalog/export/markdown` e a escrita `POST`/`PUT`/`DELETE /exercise-mapping-catalog[/{id}]`. `GET /exercise-mapping-catalog` e `/groups` continuam com consumidores (`/equipamentos-exercicios`, `/registro-exercicios/[id]`, modo treino, `workout_plan_ai`).

  **Fora da F2.4 (2026-10-01): `/treinos` APOSENTADA → 301 para `/dashboard/treinos`.** Decisão do usuário, depois que o #294 levou "Resumo da semana", "Evolução por semana ISO", "Distribuição de RPE" e a "Análise por IA" para `/dashboard/treinos` e o #295 levou a "Ativação muscular (últimos 7 dias)" (com o cruzamento de recuperação entre dias consecutivos e as metas dos macro-grupos) para `/dashboard/musculos` (`MuscleActivation7d`). "Cardio & Recuperação" e "Recuperação muscular" não migraram por duplicidade: já existiam em `/dashboard/cardio` e `/dashboard/recovery`. Mesmo padrão das anteriores: 301 na CloudFront Function (casa só `/treinos` e `/treinos/...`; `/dashboard/treinos`, `/modelos-treino`, `/planos-treino` e `/modo-treino` cobertos por teste) + `LegacyPathRedirect` de fallback; item sai do menu (o item "Treinos" do hub `/dashboard/treinos` fica). Órfão removido: `guidanceLabel` (`lib/muscle-readiness.ts`). **Nenhum endpoint removido e nenhum ficou sem consumidor**: `GET /analytics/training-evolution`, `/training-trend`, `/rpe-distribution` e `POST /workouts-analysis/generate` seguem em `/dashboard/treinos` (via `lib/dashboard-api.ts`); `GET /analytics/muscle-groups-daily-activation` e `/muscle-recovery-consecutive-check` seguem em `/dashboard/musculos` (`MuscleActivation7d`).

  **Fora da F2.4 (2026-10-01): `/relatorios` APOSENTADA → 301 para `/dashboard/relatorios`.** Decisão do usuário: o relatório em PDF com 5 análises de IA (assíncrono: Lambda `musclefit-reports`, S3 e polling; menu desativado desde o #37) foi **substituído** por um relatório em **Markdown**, só com dados, gerado na hora pela api-core (`GET /reports/markdown?window=30d|90d|180d|custom`, custom de 7 a 180 dias) e baixado em `/dashboard/relatorios`. O coletor (`intelligence/report/markdown_report_service.py`) reaproveita os serviços v3 (`build_muscle_summary`, `build_recovery_signals`, `build_body`, `build_cardio`, `build_adherence`, `segment_window`, macro-grupos da 057) e só tem duas leituras próprias: a tonelagem por grupo ciente de `load_convention` (assistido/peso corporal fora, séries excluídas declaradas) e as médias de sono/wearable. **Removidos:** `POST /reports/generate`, `GET /reports`, `GET /reports/{id}`, `GET /reports/{id}/pdf`, `DELETE /reports/{id}`, `reports_core.py`, `report_pdf_builder.py`, o template HTML do PDF e as dependências WeasyPrint/matplotlib/Jinja2 (e as libs apt do WeasyPrint na imagem da api-core). **Ficam:** a tabela `reports` (sem migração destrutiva), `ReportDataService.build_rag_context` e `report_ai.analyze_training_evolution` (ainda usados pela "Análise por IA" de `/dashboard/treinos`), e — até o PR de infra — `lambda_reports.py` (agora um handler que só marca o job como descontinuado) e `api/Dockerfile.reports`, porque o smoke de imagens do CI importa esse handler. Melhoria registrada: a tonelagem do dashboard (`SESSION_EQUIPMENT_SQL`, `_fetch_group_tonnage_rows`) ainda ignora `load_convention`.

  **M31 continua vivo.** `get_consecutive_day_recovery_checks` e `GET /analytics/muscle-recovery-consecutive-check` têm um único consumidor, `/treinos` (`dashboard_modules.py:72`), e `/treinos` **não** foi aposentada. O M31 morre no PR que remover a rota, não antes.

**Fase 3**
- F3.1 `/dashboard/engine` sobre `generation_runs` (migração 054, **entregue no M16**) + `progression_decisions` (planejado vs gerado vs executado) — **ENTREGUE**.

  Dois endpoints novos, sem migração (a 054 já dava tudo): `GET /analytics/dashboard/engine` (as gerações da janela, **2 consultas**) e `GET /analytics/dashboard/engine/plans/{plan_id}` (as três colunas item a item, **6 consultas fixas** + 1 condicional para nomear os candidatos recusados). Serviço em `intelligence/dashboard/engine_service.py`; contratos aditivos em `contracts.py` (`EngineRunsResponse`, `EnginePlanResponse` e os blocos `Engine*`); métricas `generation-audit-v1` e `engine-diff-v1` no registry.

  Quatro decisões:

  1. **Duas fontes, preferência pela tabela e fallback DECLARADO.** `generation_runs` nasceu vazia em 2026-09-26 e não há backfill possível (a data da geração não existe para o passado). Fichas anteriores caem em `workout_plans.raw_payload.generation_audit`, e a resposta diz qual fonte valeu (`EngineAuditSource`): do `raw_payload` não sai data de geração, `job_id` nem `request_json`, e apresentar a ficha antiga como se tivesse essas informações seria indistinguível de defeito. A leitura do `raw_payload` vem no `JSON_EXTRACT` do próprio cabeçalho da ficha — o fallback não custa uma segunda ida ao banco.
  2. **A coluna "planejado" não nomeia exercício quando o Modelo não nomeou.** No modo `percent_budget` o Modelo pede VOLUME por grupo: a coluna mostra o orçamento (`generation_audit.activation`) com a ressalva escrita. Casar um item `muscle_group` do Modelo com um exercício específico atribuiria ao Modelo uma decisão que foi do motor. No `equipment_series` o casamento é pelo `mapping_id`, e cada pedido do Modelo casa **uma** vez (um Modelo pode pedir o mesmo exercício duas vezes).
  3. **Nenhuma regra de status é reescrita.** `COMPLETED/INTERRUPTED/SKIPPED/SUBSTITUTED/EXTRA`, o prescrito com fonte declarada e a junção ficha × executado vêm inteiros de `adherence_service.build_session_items` — uma segunda implementação criaria duas respostas possíveis para "este item foi pulado?" (a dupla verdade do §18).
  4. **O efeito do PR #211 é reconhecido, não corrigido à força.** Item sem séries próprias cujo exercício foi registrado no slot de OUTRO item da mesma ficha continua `SKIPPED` (é a verdade sobre o vínculo) e ganha a limitação que diz onde a execução foi parar — mesma regra de `findPlanItemsCoveredByAnotherSlot`, agora do lado do servidor.

  A rota v2 `GET /analytics/dashboard-engine-audit` **não foi removida**: ela entrou em `legacy_endpoint_telemetry` com sucessor `/analytics/dashboard/engine`. Medir antes de remover — ela só pôde ser marcada agora porque até esta fase era a implementação viva da tela.

  **Fora desta entrega** (candidatos a M): o `request_json` da geração (o pedido que a originou) é gravado pela 054 mas ainda não é exibido; o detalhe de uma geração cujo rascunho **não** virou ficha aparece na lista, sem tela própria (não há `plan_id` por onde abri-la); o link para `template_generation_logs` ("como o MODELO foi gerado") não foi adicionado; e não há comparação entre DUAS gerações do mesmo slot.
- F3.2 **(entregue)** `intelligence_package` + `/insights/generate` server-side + endpoint para PT + avaliação de respostas.

  `app/intelligence/dashboard/intelligence_package.py` é um **montador**, não um serviço de cálculo: ele achata o que `build_overview` já produziu numa única passada. Por isso o pacote custa **exatamente os mesmos statements do overview** (o overview já lia eventos de contexto, grupos musculares e intervalo desde a última sessão para alimentar as regras — o pacote só ganhou um caminho de saída para eles), afirmação travada em `tests/integration/dataapi/test_medicao_dataset_pequeno.py` com `==`, não com teto.

  Contrato fechado `IntelligencePackageResponse` (`contracts.py`): `question?`, `window`, `timezone`, `package_version`, `metrics[]` (achatadas do overview, cada uma com `nature`/`metric_version`/`data_quality`), `baselines[]`, `evidence[]` (com `insight_code`, `severity` e `observed_at`), `context_events[]`, `muscle_groups[]`, `data_quality`/`data_quality_overall`, `limitations[]`, `allowed_actions[]` (vocabulário FECHADO), `truncated`, `size_bytes`. Teto de **40 KB**: a defesa primária são os tetos por lista (`MAX_CONTEXT_EVENTS`/`MAX_EVIDENCE`/`MAX_MUSCLE_GROUPS`/`MAX_NOTES_CHARS`, dimensionados juntos para o pior caso plausível caber sem cortar); o corte por bytes é a rede, e quando acontece é **declarado** em `limitations` com a ordem exata (`context_events > evidence > muscle_groups`) — métrica e baseline nunca são cortadas.

  `POST /insights/generate` passou a receber só `question` + `window` e a montar o contexto no servidor. O RAG deixou de ser o dump completo de `rag/markdown` e passou a ser `rag_chunking.select_rag_text(grupos_do_pacote, has_training_type=True)` — o `has_training_type=True` é deliberado: sem ele o fallback recursivo de `TIPOS_TREINO/**` traria o dump de volta por outro caminho. **Janela de compatibilidade**: um cliente antigo que ainda mande `context` é ignorado com `log_event insights.legacy_context_ignored`, não recusado com 422 — frontend e API são artefatos separados e não sobem no mesmo instante. O campo sai num PR posterior, depois de o log mostrar zero ocorrência.

  Prompt em **um lugar versionado** (`app/insights_prompt.py`, `PROMPT_VERSION`), com as três regras literais (não recalcular, não inventar, não afirmar causalidade), a exigência de citar `evidence[]` **pela data** e a proibição de sugerir ação fora de `allowed_actions`. `insights_ai.py` perdeu a constante de módulo com a chave da API (o valor congelava no import: rotação de segredo só valia no próximo cold start) e passou a chamar `openai_client.call_openai`, como todo o resto do projeto.

  `GET /analytics/dashboard/intelligence-package` (Lambda `engines`, cai no resource `dashboard` que já existe no API Gateway). **Escopo estrutural**: a rota não tem parâmetro `user_id` — o dono é sempre `identity.user_id`. Ler o pacote de outra pessoa não é permissão negada, é chamada que não tem como ser escrita. Rate limit por usuário (30/60 s, em memória do processo — contenção declarada, não defesa contra abuso distribuído) e log de acesso (`intelligence_package.accessed`, com origem da identidade).

  **Token do PT externo: proposta, não entregue.** Reusar `devices` como está seria errado: `security.device_token_matches` resolve qualquer token de dispositivo para a identidade **admin** (`_admin_user_id`), o que daria ao PT escrita em tudo. O caminho recomendado continua sendo `devices` (já tem hash SHA-256, `revoked_at`, `is_active`, `last_seen_at` — uma tabela nova duplicaria isso), com duas colunas na migração **055**: `devices.user_id` (dono real, em vez do admin) e `devices.scope` (`full` | `read_intelligence`). O que trava é o alcance: `resolve_identity` alimenta MCP, Telegram e o pareamento do relógio Zepp, e escopar o token exige auditar todos esses consumidores. **Correção de numeração (Fase 4, PR B):** o número **055** foi ocupado por `055_muscle_groups_porte_e_agregacao.py` (porte e agregação de grupos musculares, fora da sequência do Novo Dashboard); quando esta proposta for executada, ela recebe o próximo número livre. Isso é decisão de segurança do usuário, não efeito colateral desta entrega. Enquanto ela não vem, o PT consome a rota com credencial do próprio usuário.

  **Avaliação de respostas** em duas metades (`api/tests/eval/README.md`): (a) determinística, em `tests/unit/test_package_eval.py`, roda em todo PR — pergunta "a IA **tinha como** responder bem?" (o pacote contém as métricas, baselines e evidências que a resposta citaria, cabe no teto, não carrega identificador de usuário); (b) com LLM, `tests/eval/test_llm_insights_eval.py`, marcador `llm_eval` **excluído do `addopts`** — custa dinheiro e não é determinística, roda sob demanda via pytest ou via `python -m tests.eval.run_llm_eval`. O conjunto `tests/eval/questions.json` declara `prompt_version`/`package_version`, e o teste falha se o prompt mudar sem o conjunto ser revisto: sem isso, uma piora depois de uma edição de prompt seria indistinguível de variação do modelo.

Fora do MVP (registrar como M no backlog): tabela de equivalência de equipamentos; versionamento de metas por data; reapontar FKs de `exercise_zepp_mappings`; cache por usuário/janela. ~~histórico de `assignments`~~ — **entregue no M23** (migração 049): `unassigned_at` + UNIQUE de uma vigência por usuário, com a resolução por data (a "fronteira de versão do plano" do §4) hoje **permanente em todos os caminhos** (decisão do usuário: aplicar retroativo). A flag `DASHBOARD_PLAN_VERSION_BOUNDARY` foi **aposentada em 2026-10-08** (COD-01): a variável e o ramo "desligada" foram removidos; não há migração reversa.

---

## 18. Riscos, decisões e alternativas descartadas

- **Risco** custo de `get_muscle_group_overview` (histórico completo por chamada) — mitigação: janelas + memo por request; medir antes de cachear.
- **Risco** limite 1 MB Data API — mitigação: paginação e caps.
- **Risco** rotas em 3 lugares — mitigação: teste de paridade existente cobre.
- **Decisão**: novos endpoints em `engines` (sem OpenAI, já tem os dashboards). Descartado: api-core (misturaria com CRUD e Lambda com segredo OpenAI).
- **Decisão**: evoluir hub v2, não criar terceiro dashboard. Descartado: reescrever do zero.
- **Decisão**: status por exercício em códigos ingleses novos, mantendo `PROGREDINDO/...` nas rotas v2 até aposentadoria. Descartado: renomear os existentes (quebra contrato).
- **Decisão**: derivar pulado/substituído/interrompido em vez de coluna. Descartado: `execution_status` (dupla verdade).
- **Decisão**: sem score único; bloco "Progressão" mostra contagens por status.
- **Descartado**: mapa corporal como navegação principal; pie charts; ML preditivo.

---

## 19. Perguntas bloqueantes

Nenhuma. Respondidas em 2026-09-22: consolidar telas antigas (sim), `load_convention` por par (sim), contexto manual (sim).

---

## 20. Checklist de aceite

- [ ] Cada pergunta central tem endpoint, fórmula versionada, baseline, regra, tela e drill-down (§7).
- [ ] Envelope `Metric` com `nature` em todo valor exibido; `null` ≠ 0.
- [ ] Fonte canônica única por domínio (§4); nenhuma regra de cálculo no frontend (F0.4/F2.4).
- [ ] Aquecimento/ajuste fora das séries efetivas; planejado/realizado/projetado nunca somados na mesma métrica.
- [ ] Comparação só com `comparability_key` igual; `load_convention` respeitada.
- [ ] Ciclo arbitrário e comparação por exposição (testes §16).
- [ ] Insights com evidência, confiança, limitações, ação e reavaliação; máx. 2 na home.
- [ ] Ownership por `user_id` em todo endpoint novo (teste A/B).
- [ ] Migrações 044–047 aditivas com teste de plano Aurora.
- [ ] Mobile verificado em iPhone antes de F1 ser considerado pronto.
- [ ] Ambiente DEV atualizado (pull, containers, migrações, seed) antes de pedir validação.

**READY TO IMPLEMENT**

## Verificação end-to-end (por PR)
1. `make test-unit` e `make test-integration` verdes (dois backends); `cd frontend && npm test && npm run typecheck && npm run smoke:export`.
2. CI do PR verde; base `develop`; descrição com "Impacto em produção".
3. Após merge: atualizar DEV (`make up`, migrações, `make seed`), abrir `/dashboard` em desktop e iPhone, conferir `null`/estados e `data_quality`; só então pedir validação ao usuário. Nunca mesclar em lote sem avisar.

# Módulo de Nutrição — Plano de implementação

Data: 2026-10-14. Repositório `C:\muscle_fit`, branch `develop` (HEAD `329dc79`). Estado: **plano aprovado pelo usuário, nada implementado**. Todo caminho de arquivo citado foi verificado no código em 2026-10-14; a base científica e as licenças das tabelas de alimentos foram verificadas na web na mesma data (fontes no §16).

Decisões do usuário (2026-10-14): (1) a TBCA não é embutida sem autorização escrita dos coordenadores; TACO + USDA + Open Food Facts são as bases do catálogo; (2) macros de refeição em modo híbrido: a IA interpreta a descrição e sugere o casamento com o catálogo, o motor calcula; (3) personal trainer segue sem vínculo formal com aluno (padrão atual de Modelos e Metas), com o acesso centralizado em um módulo só para trocar depois; (4, 2026-10-15) kcal e FC média do treino vêm do sync do Intervals.icu já existente (`intervals_sync.py` → `activity_intervals_link` → `activities`), nunca do app do relógio Zepp.

Regras de operação (CLAUDE.md): motores como fonte única da verdade (`docs/motores/FONTES_DA_VERDADE.md` + guarda `api/tests/unit/test_guarda_fontes_da_verdade.py`); nada aplicado à mão na AWS; toda implementação delegada a agentes especialistas; PRs pequenos contra `develop`; paridade dev/prod; IA só em Lambda assíncrona.

Como usar este documento: cada lote do §13 vira um briefing de agente que cita este plano, a regra dos motores, o mapa e a guarda. O §15 lista as decisões que ainda cabem ao usuário ao abrir cada lote. O §17 registra o estado de execução e deve ser atualizado a cada lote mesclado.

---

## 1. Contexto

O app hoje cobre treino (fichas, Modelo, progressão, recuperação, volume, cardio) e dados
corporais (bioimpedância, fita métrica, peso). O usuário quer fechar o ciclo
hipertrofia + definição com um **plano nutricional** prescrito por motor determinístico,
definido por admin ou personal trainer, com registro de refeições/alimentos apoiado por IA,
tabelas brasileiras (TBCA/TACO) e USDA como reserva, cálculos por dia e por semana,
ciclos de carboidrato, períodos de bulking/cutting e dashboards alimentação × gasto.

Nada será implementado agora: o resultado é um plano completo para implantação futura,
respeitando as regras permanentes do projeto (motores como fonte única da verdade; tudo via
PR na `develop`; paridade dev/prod; IA só em Lambda assíncrona).

## 2. O que já existe e será reaproveitado (verificado no código em 2026-10-14)

| Área | O que existe | Onde | Lacuna |
|---|---|---|---|
| Bioimpedância | `body_composition` com `weight_kg, bmi, body_fat_percent, fat_mass_kg, fat_free_mass_kg, muscle_mass_kg, skeletal_muscle_percent, body_water_percent, visceral_fat_rating, bone_mass_kg, bmr_kcal, protein_percent, metabolic_age, source, local_date` | `api/db/migrations/001_schema.sql:376-400`, `api/app/body_composition.py`, tela `indicadores-fisicos/BioimpedanciaSection.tsx` | `bmr_kcal` da balança não é lido por nenhum motor |
| Fita métrica | `body_measurements.site ENUM(waist,hip,chest,arm_l,arm_r,thigh_l,thigh_r,calf_l,calf_r)` | `047_body_measurements.py`, `api/app/body_measurements.py` (`MEASUREMENT_SITES`) | sem `neck` (necessário para % gordura por circunferência) nem antebraço |
| Perfil | `users.birth_date`, `users.sex` (texto livre), `timezone`, `preferred_weight_unit` | `api/app/users.py`, `perfil/PerfilClient.tsx` | **sem altura**, sem nível de atividade; sexo não normalizado; `/auth/me` não devolve sexo/nascimento; nenhum motor lê idade/sexo |
| kcal do treino | `activities.calories_kcal` gravável por `POST /workout-sessions/{id}/finish` (`SessionFinishIn`) e pelo sync do Intervals (`COALESCE`) | `workout_sessions.py:129-135, 677-763`, `intervals_sync.py:136-142` | web manda `{}` no finish; `PATCH /meta` (`SessionMetaIn`, `workout_sessions.py:329-406`) não aceita kcal; tela do treino não mostra kcal. **`PUT /activities` recria a linha: não usar**. Medido em produção em 2026-10-15: 34/34 sessões `done` desde 09/2026 têm kcal e FC vindas do Intervals (valor igual ao do link em 100%); 47 sessões de jul–ago/2026 anteriores ao sync estão sem link (24 com `started_at`, 23 sem), todas com `WeightTraining` no mesmo dia em `intervals_activities` (150/150 com kcal e FC) |
| Biometria diária | `daily_biometrics.weight_kg, steps, calories_active_kcal, calories_total_kcal` | `001_schema.sql:360-374` | sem escritor; sem endpoint (fica fora do escopo) |
| Contexto | `context_events.type = diet_phase` | `048_context_events.py`, `context_events_schema.py:59` | só rótulo, sem estrutura nem consumidor |
| Check-in | `daily_checkins` energy/soreness/stress/mood 1–5 | `050` | sem fome/hidratação |
| Dashboard Corpo | `body_service.py`: peso média móvel 7 d (`weight-ma-v1`), cintura, bioimpedância, sinal de recomposição `recomp-v1` | `intelligence/dashboard/body_service.py`, `/dashboard/corpo` | não lê TMB; sem gasto/ingestão |
| Papéis | `users.role` ENUM admin/regular/personal_trainer; `require_admin_or_pt` | `security.py:392-413` | API não permite atribuir PT (`users._ALLOWED_ROLES = {"admin","regular"}`, `users.py:42`); sem vínculo PT↔aluno (PT vê qualquer usuário) |
| IA assíncrona | rota 202 → `async_jobs.create_job` + `invoke_async("WORKOUT_AI_FUNCTION_NAME")` → `lambda_workout_ai.py` → `workout_ai_generation_core.dispatch_*` → `<x>_ai.py` → `openai_client.call_openai_structured` | `main.py:414`, `async_jobs.py:52-72` (`JOB_TYPE_LAMBDA`), `openai_client.py:551` | **sem entrada de imagem** (`input` é só string); `feature` nova precisa de teto em `LLM_OUTPUT_OBSERVED_MAX_TOKENS` (`openai_client.py:118`) |
| Upload de foto | multipart ≤ 8 MB pela api-core (`gyms.read_limited_photo`, `sniff_image_content_type`, `_save_photo`) → S3 `musclefit-uploads` | `gyms.py:43-110, 219`, `infra/s3_uploads.tf:119-167` (lifecycle único) | sem resize; Lambda síncrona aceita 6 MB de payload |
| Cota de IA | periodização 10/dia (`periodization/logs.py`); cards 100/dia | — | sem cota genérica; custo só no log `llm.usage` (14 d) |
| RAG | `.md` na raiz de `rag/markdown/` entram em TODOS os prompts de treino (`rag_chunking.discover_additional_files`), exceto `EXCLUDED_FILES` | `rag_chunking.py:171, 433` | nutrição precisa de subpasta própria; insights de treino excluem dieta de propósito (`criterios_llm.FORA_DO_ESCOPO`, `insights_prompt.py:91`) — manter |
| Vigência | `effective_from`/`superseded_at` + coluna gerada `current_*` com UNIQUE | `052_training_type_target_versioning.py`, `049_template_assignment_history.py` | padrão a copiar |
| Motor dono | docstring com §, constantes `Final`, dataclass `frozen` com `to_dict`, `export_for_frontend()`; guarda `Padrao`+`ALLOWLIST`+`EXEMPLOS`; espelho FE por `test_m76_lote1b_espelhos_fe.py --gravar` | `domain/cardio_policy.py`, `domain/next_set_load.py`, `docs/motores/FONTES_DA_VERDADE.md` §5/§7 | — |
| Migrações | `.py` com `plan(db)`/`up(db)` idempotente, `EXPECTED_COLUMNS`, teste com `FakeDB` | `067_...py`, `072_...py`, `test_migration_072_plano.py` | próximo número: **074** (a 073 é a do modo de contagem do volume, já em produção); a nutrição usa 074–086 (§6.1; a 084 é a do M85) |
| Código morto | `body_composition_trend_service.get_physical_summary`, `training_load_service.get_daily_activity_summary` | — | apagar no L0 |
| Relógio Zepp | fora do plano (decisão de 2026-10-15): kcal e FC chegam pelo sync do Intervals, não pelo app do relógio | `api/app/intervals_sync.py`, `api/db/migrations/041_intervals_hr_link.py` | nenhum pedido ao agente `zepp` |

## 3. Base científica do motor (fontes verificadas em 2026-10-14)

Toda constante abaixo vira `Final` nomeado no módulo dono; prompt/RAG/FE só interpolam.

### 3.1 Taxa metabólica basal (TMB)
- **Mifflin-St Jeor** (padrão sem % gordura; Frankenfield 2005: mais confiável dentro de ±10%): H = 10·kg + 6,25·cm − 5·idade + 5; M = … − 161.
- **Katch-McArdle** (padrão com massa livre de gordura, MLG, da bioimpedância): 370 + 21,6·MLG.
- **Cunningham 1980** (variante para muito treinados): 500 + 22·MLG. Só comparação/opção.
- `body_composition.bmr_kcal` (balança) só referência exibida, nunca dono.
- Seleção `auto`: MLG disponível e recente (≤ 60 d) → Katch-McArdle; senão Mifflin. Admin/PT pode fixar.

### 3.2 Gasto energético total (TDEE)
- TDEE = TMB × fator de atividade **de não-exercício** + gasto do treino do dia (explícito, evita dupla contagem). Modo alternativo `included_in_factor` com os fatores clássicos (1,2 / 1,375 / 1,55 / 1,725 / 1,9). TEF já embutido nos fatores.
- **Gasto do treino de força**, prioridade: (1) kcal observada (sync do Intervals via `activity_intervals_link` → `activities.calories_kcal`, ou digitada à mão; `OBSERVED`) → (2) MET × duração (Compendium 2024: 02050 vigoroso 6,0; 02052 agachamento/terra 5,0; 02054 múltiplos exercícios 8–15 reps 3,5 = default; 02055 circuito 5,8): kcal = MET × 3,5 × kg / 200 × min (`ESTIMATED`). Estimativas **calculadas na leitura, nunca gravadas**.
- Keytel removido em 2026-10-15 (D-K); o L1 implementou só kcal observada → MET. A fórmula de FC (Keytel 2005, referência no §16) saiu da lista porque o Intervals entrega kcal em 150 de 150 atividades de musculação (`WeightTraining`), então o caso "FC sem kcal" que ela cobriria não ocorre.
- **Gasto observado (adaptativo)**: TDEE_obs = média de ingestão (14–28 d) + Δpeso-tendência × densidade energética (Hall 2008/Forbes: p_fat = FM/(FM+10,4); gordura 9.400 kcal/kg, magro 1.800; nada de 7.700 fixo). `ESTIMATED` com qualidade; reconcilia com a fórmula só com ≥ 14 d e ≥ 80% de dias registrados; **só sugere**, admin/PT aciona recálculo.

### 3.3 Estratégias
| Estratégia | Balanço | Ritmo | Proteína | Gordura | Carbo | Fontes |
|---|---|---|---|---|---|---|
| Cutting | déficit p/ 0,5–1 %/sem (padrão 0,7), ≤ 25% do TDEE, ingestão ≥ 1,0 × TMB | ↓ 0,5–1 % PC/sem | 2,3–3,1 g/kg **MLG** (padrão 2,6) | 15–30% kcal (padrão 25%) | resto | Helms 2014; Garthe 2011 |
| Bulking | +10–20% (avançado +5–10%) | ↑ 0,25–0,5 % PC/sem | 1,6–2,2 g/kg PC (padrão 2,0) | 0,5–1,5 g/kg (20–35%) | resto, ≥ 3–5 g/kg | Iraki 2019; Morton 2018 |
| Maintenance | 0 (±5%) | 0 | 1,6–2,2 g/kg | 20–35% | resto | ISSN 2017 |
- Refeições 3–6/dia; proteína 0,40–0,55 g/kg por refeição; uma 1–2 h antes/depois do treino (benefício "menor").
- Pausas e refeeds no cutting: pausa 1 sem a cada 3 (ICECAP, Peos 2021) ou 2 on/2 off (MATADOR, Byrne 2018); refeed de carbo 2 dias/sem (Campbell 2020). Modelados como **eventos do calendário de fases**, propostos pelo motor e publicados por admin/PT.
- Ciclo de carbo: evidência fraca para composição; usado como **distribuição planejada** do orçamento semanal (dia de treino/pernas ×1,25, descanso ×0,75, piso 1 g/kg), proteína constante, média semanal = alvo; kcal do dia acompanha o carbo.

### 3.4 Composição corporal e metas
- % gordura por circunferência (fallback), Marinha dos EUA (Hodgdon & Beckett 1984): H = 86,010·log10(abd − pescoço) − 70,041·log10(altura) + 36,76 (polegadas); M = 163,205·log10(cintura + quadril − pescoço) − 97,684·log10(altura) − 78,387. Exige `neck`.
- FFMI normalizado = MLG/alt² + 6,1·(1,8 − alt); referência 25 (Kouri 1995, com ressalva). Cintura/altura < 0,5.
- **Peso-alvo** = MLG atual / (1 − %G alvo). IMC e Devine só informativos.
- Parada sugerida de bulking/cutting por %G (H 15/10, M 25/18): defaults editáveis no plano.

### 3.5 Semana
- Orçamento semanal = 7 × alvo; carbo ciclado redistribui mantendo a soma; banco com transferência ≤ 10% do alvo diário e ≤ 5% do semanal; aderência = Σ ingestão / Σ alvo (tolerância 5%; proteína ≥ 90%); semana começa na segunda; semana "válida" com ≥ 5 dias registrados.

## 4. Fontes de dados de alimentos e licenças (verificado 2026-10-14)

| Fonte | Conteúdo | Acesso | Termos | Decisão |
|---|---|---|---|---|
| **TBCA** (USP/FoRC, v7.3) | > 5.700 alimentos por 100 g | só busca no site; **sem API, sem download em massa** | divulgação não comercial com citação; **reprodução total/parcial não permitida**; comercial só com os coordenadores (tbca.contato@usp.br) | **não embutir sem autorização escrita**; adaptador plugável + entrada manual `source=tbca_manual` |
| **TACO** (NEPA/UNICAMP, 4ª ed.) | 597 alimentos por 100 g | PDF (XLS histórico não localizado oficialmente) | reprodução permitida com citação | **seed brasileiro**; pré-requisito do L3: obter versão legível por máquina (PDF→CSV ou CSV de terceiros conferido por amostra contra o PDF) |
| **USDA FoodData Central** | Foundation (CSV 32 MB), SR Legacy (54 MB) | API 1.000 req/h (chave data.gov) + CSV | CC0 | busca sob demanda + import por item (`source=usda`) |
| **Open Food Facts** | produtos por código de barras | `GET /api/v3.6/product/{ean}.json`, 15 req/min/IP, User-Agent obrigatório | ODbL/DbCL (atribuição) | rótulos industrializados; cache local (`source=off`) |
| Rótulo (RDC 429/2020 + IN 75/2020) | por 100 g/ml E por porção, %VD | — | — | schema da foto: valor energético (kcal/kJ), carboidratos, açúcares totais e adicionados, proteínas, gorduras totais/saturadas/trans, fibras, sódio, porção, porções por embalagem |

OpenAI vision (Responses API): `{"type":"input_image","image_url":"data:image/jpeg;base64,…","detail":"high"}` em `content` da mensagem `user`; ~1.000–1.500 tokens por foto 1024×1024. Structured Outputs + imagem: confirmar no 1º teste pago do L6 (fallback `json_object` + validação Pydantic).

## 5. Decisões de desenho (D1–D3 confirmadas pelo usuário em 2026-10-14)

- **D1 — PT↔aluno**: padrão atual (`require_admin_or_pt`, PT vê/define para qualquer usuário); `nutrition_access.resolve_target_user` centraliza para trocar depois; vínculo formal vira L11. O L0 libera `personal_trainer` em `_ALLOWED_ROLES`.
- **D2 — Macros da refeição**: híbrido. IA (a) interpreta a descrição em itens (alimento, quantidade, unidade) e (b) sugere o casamento com o catálogo; o motor calcula macros a partir do catálogo (`DERIVED`). Sem item no catálogo → IA estima (`ESTIMATED`, destacado) e o item vira candidato a cadastro.
- **D3 — TBCA**: não embutida sem autorização; TACO + USDA + OFF são as bases; adaptador TBCA plugável.
- **D4 — Plano versionado**: admin/PT define estratégia + parâmetros por usuário; motor gera metas e propõe calendário; admin/PT publica (`effective_from`/`superseded_at`, uma vigente por usuário via coluna gerada + UNIQUE).

Decisões estruturais do desenho:
| # | Decisão | Motivo |
|---|---|---|
| E1 | Motor em `api/app/domain/nutrition/` (pacote) | já está em `MODULOS_DE_MOTOR` da guarda; `export_for_frontend()` segue os vizinhos |
| E2 | Metas diárias **materializadas sob demanda e congeladas** (`nutrition_daily_targets`) | meta de dia passado não muda quando peso/MLG mudam; SQL/dashboard leem valor pronto; sem scheduler |
| E3 | Semana derivada (sem tabela): `weekly_budget.py` soma 7 metas + 7 dias | sem dupla verdade |
| E4 | Macros congeladas no item (`meal_items.*_g` + `catalog_snapshot_json` + `engine_version`) | catálogo muda de versão; histórico reproduzível |
| E5 | Catálogo "preguiçoso": TACO seed; USDA/OFF sob demanda com cache em `food_items` | licenças; sem download em massa |
| E6 | kcal do treino: informada = `OBSERVED` (gravada); estimativa MET calculada na leitura (`ESTIMATED`), nunca gravada (Keytel removido em 2026-10-15, D-K) | uma verdade só; mudar fórmula sem backfill |
| E7 | Tabela genérica `ai_usage_log` para as features de nutrição | cota = `COUNT` por (user, feature, dia); as antigas não migram agora |
| E8 | Jobs: texto → `musclefit-workout-ai` (60 s); imagem → `musclefit-plan-generator` (180 s) | nenhuma Lambda nova |
| E9 | Preview de macros no FE = chamada à API (`POST /nutrition/meals/preview`) | nada de regra numérica no FE |
| E10 | Fases em `nutrition_phase_events`; `context_events.diet_phase` derivado na leitura | sem dupla verdade |

## 6. Modelo de dados

### 6.1 Migrações (modelo `plan(db)`/`up(db)` das 067/072; FakeDB em teste)
| Nº | Arquivo | Faz | Depende | Lote |
|---|---|---|---|---|
| 074 | `074_users_anthropometrics.py` | `users` + `height_cm DECIMAL(5,1) NULL`, `activity_level VARCHAR(16) NULL`; não toca `sex` (backfill para `male`/`female`/`other` na 077, L2a, ver §13) | 001 | L0 |
| 075 | `075_body_measurements_sites.py` | `MODIFY site ENUM(…existentes…, 'neck','forearm_l','forearm_r')` **acrescentando no fim** (metadata-only no MySQL 8); `plan` compara `COLUMN_TYPE`; `MEASUREMENT_SITES` e o teste da 047 passam a comparar com a 075 | 047 | L0 |
| 076 | `076_activities_calories_source.py` | `activities` + `calories_source VARCHAR(16) NULL` (`manual`/`intervals`); sem backfill (leitor assume `intervals` quando `activities.calories_kcal` = `activity_intervals_link.calories_kcal`) | 001 | L0 |
| 077 | `077_users_sex_canonical.py` | backfill idempotente de `users.sex` para `male`/`female`/`other` (aliases de `normalize_sex` embutidos, comparação exata com `COLLATE utf8mb4_0900_bin`; NULL e desconhecido ficam; `plan_down` no-op). Produção: 2 statements, 2 linhas | 001 | L2a |
| 078 | `078_nutrition_plans.py` | `nutrition_plans` | 074 | L2b |
| 079 | `079_nutrition_daily_targets.py` | `nutrition_daily_targets` | 078 | L2b |
| 080 | `080_food_catalog.py` | `food_items` + `food_portions` (FULLTEXT criado aqui, antes do seed) | — | L3 |
| 081 | `081_food_catalog_seed_taco.py` | seed TACO 4ª ed. (597 linhas **embutidas no próprio módulo** para ficarem sob o checksum; `INSERT … ON DUPLICATE KEY UPDATE` em blocos ≤ 50 linhas por statement, por causa do tamanho de statement da Data API) | 080 | L3 |
| 082 | `082_meals.py` | `meals` + `meal_items` | 080 | L4 |
| 083 | `083_nutrition_days.py` | `nutrition_days` | — | L4 |
| 084 | `084_food_items_taco_usda_reavaliacao.py` | `food_items.field_sources_json` + campos `*` da TACO com o USDA | 080, 081 | M85 |
| 085 | `085_ai_usage_log.py` | `ai_usage_log` | — | L5a |
| 086 | `086_nutrition_phase_events.py` | `nutrition_phase_events` | 078 | L8 |

Regras: InnoDB utf8mb4; `created_at`/`updated_at`; FK só para `users(id) ON DELETE CASCADE` e `meal_items → meals`; **referência lógica + índice** para `food_items`, `nutrition_plans`, `activities`; códigos em `VARCHAR` (vocabulário do motor); ENUM só em domínio pequeno nosso (`site`); nenhuma FK para `exercise_catalog`.

### 6.2 DDL resumido
**`nutrition_plans`** (078, L2b): `id`, `user_id` FK `ON DELETE CASCADE`, `created_by`/`published_by` (lógicos), `status` (`draft`/`published`), `strategy` (`cutting`/`bulking`/`maintenance`), `advanced` (faixa de superávit do avançado), `bmr_formula` (`BmrFormula`: `auto`/`mifflin_st_jeor`/`katch_mcardle`/`cunningham`/`harris_benedict`), `activity_level` (override do perfil, NULL = o do perfil), `training_kcal_mode` (o `TdeeMode` do motor: `explicit_training`/`included_in_factor`; na API o campo é `tdee_mode`), `goal_body_fat_percent`, `goal_weight_kg`, `weekly_rate_share` (cutting), `surplus_share` e `carb_min_g_per_kg` (bulking), `protein_g_per_kg`, `fat_kcal_share`, `meals_per_day`, `carb_cycling_json`, `refeed_days_per_week` e `diet_break_pattern` (`BreakProtocol`: `none`/`matador`/`icecap`) — os três do L8, NULL até lá —, `notes`, `engine_version`, `effective_from DATE` (dia do aluno), `superseded_at`, `published_at`, `created_at`, `updated_at`, `current_user_id` GENERATED `IF(status='published' AND superseded_at IS NULL, user_id, NULL)` **UNIQUE**; `KEY (user_id, effective_from)`. Nomes e códigos são os do motor (`domain/nutrition`); sem conversão de percentual na API.

**`nutrition_daily_targets`** (079, L2b): `user_id` FK, `local_date`, `plan_id` (lógico, com índice), `day_kind` (`carb_cycling.DayKind`: `high`/`normal`/`low`), `is_training_day`, `training_known` (decisão do motor gravada: 0 = dia futuro, treino desconhecido), `phase_kind` (L8, NULL), `kcal_target`, `protein_g`, `carbs_g`, `fat_g`, `fiber_g` (NULL = não calculável, nunca 0), `water_ml` (NULL até existir regra de água no motor), `bmr_kcal`, `bmr_formula` (efetiva), `tdee_kcal` (gasto total do dia), `training_kcal` + `training_kcal_nature` (informada × MET), `inputs_json` (sujeito, plano, contexto do dia e cada etapa do motor), `limitations_json`, `inputs_hash` (sha256 das entradas, para não regravar o que não mudou), `engine_version`, `computed_at`, `created_at`; `UNIQUE (user_id, local_date)`. Sem `data_quality` (a natureza da kcal do treino carrega a qualidade).

**`food_items`**: `source` (`taco`/`usda`/`off`/`tbca_manual`/`user`/`label_photo`/`ai_estimate`), `source_id`, `source_version` (`UNIQUE (source, source_id)`), `barcode` (KEY), `name`, `name_normalized` (FULLTEXT + prefixo), `brand`, `category`, `basis` (`g`/`ml`), **por 100 g/ml**: `energy_kcal, protein_g, carbs_g, sugars_total_g, sugars_added_g, fat_g, fat_saturated_g, fat_trans_g, fiber_g, sodium_mg`; `serving_size_g`, `serving_label`, `license`, `citation`, `owner_user_id` (NULL = público), `label_s3_key`, `is_active`, `created_by`.
**`food_portions`**: `food_id` (lógico), `label`, `grams`, `source`. Unidades caseiras genéricas vivem em `meal_math.GENERIC_PORTIONS`.

**`meals`**: `user_id`, `local_date`, `meal_slot` (`breakfast`…`supper`/`other`), `eaten_at`, `description_raw`, `source` (`manual`/`ai_text`/`ai_photo`), totais congelados (`kcal, protein_g, carbs_g, fat_g, fiber_g, sodium_mg`), `totals_nature` (`DERIVED`/`ESTIMATED`/`MIXED`), `engine_version`, `ai_job_id`, `notes`; `KEY (user_id, local_date)`.
**`meal_items`**: `meal_id` FK CASCADE, `position`, `food_id` (lógico), `item_label`, `quantity`, `unit` (`g`/`ml`/`portion:<id>`/`generic:<key>`/`unit`), `grams_resolved`, macros congelados, `nature`, `match_confidence`, `catalog_snapshot_json`.

**`nutrition_days`**: `user_id`, `local_date`, `water_ml`, `hunger` 1–5, `notes`; `UNIQUE`. Peso do dia continua em `body_composition` (`source='manual'`).
**`ai_usage_log`**: `user_id`, `feature`, `job_id`, `prompt_version`, `model`, `status` (`pending`/`succeeded`/`failed`), `tokens_in`, `tokens_out`, `image_count`, `cost_usd`, `started_at`, `finished_at`; `KEY (user_id, feature, started_at)`, `KEY (status, started_at)`.
**`nutrition_phase_events`**: `user_id`, `plan_id` (lógico), `kind` (`bulking`/`cutting`/`maintenance`/`diet_break`/`refeed`), `local_date`, `end_local_date`, `source` (`engine`/`manual`), `notes`.

### 6.3 Código ligado ao schema
- `users.py`: `_ALLOWED_ROLES` + `personal_trainer`; validação de `sex` canônico, `height_cm` 100–250, `activity_level` (códigos do motor); `/auth/me` devolve `sex`, `birth_date`, `height_cm`, `activity_level`.
- `request_models.py`: `UserProfileUpdateIn` + campos; `SessionMetaIn` + `calories_kcal`, `calories_source`; novos `NutritionPlanIn`, `FoodItemIn`, `FoodPortionIn`, `MealIn`/`MealItemIn`, `NutritionDayIn`, `MealParseIn`, `UsdaImportIn` (todos `_StrictIn`).
- `body_measurements.py`: `MEASUREMENT_SITES` + 3 sites; rótulos no FE passam a vir do JSON gerado (bloco `body_measurement_sites`).
- Apagar código morto (princípio 5): `get_physical_summary`, `get_daily_activity_summary`.

## 7. Motor — `api/app/domain/nutrition/`

Python puro, sem I/O, constantes `Final`, dataclasses `frozen=True` com `to_dict()`, `None` = não calculável. `ENGINE_VERSION = "nutrition-v1"`. Orquestrador puro `daily_target.compute_daily_target(subject, plan_params, day_context, scale_bmr=None) -> DailyTarget` encadeia `bmr → tdee → kcal_target → macro_split → carb_cycling/phase`; é o único ponto que a api-core chama para materializar.

| Módulo | Dono de | Constantes (fonte) | Funções públicas |
|---|---|---|---|
| `anthropometrics.py` | sexo canônico, idade, IMC, nível de atividade | `Sex`, `ActivityLevel`; `ACTIVITY_FACTORS` 1,2/1,375/1,55/1,725/1,9; `NON_EXERCISE_ACTIVITY_FACTORS` 1,2/1,3/1,4/1,5/1,6 (D-A); `HEIGHT_CM_RANGE` (100, 250) | `normalize_sex`, `age_years`, `bmi`, `Subject` |
| `bmr.py` | fórmulas de TMB e escolha | Mifflin (10; 6,25; 5; +5/−161), Katch (370; 21,6), Cunningham (500; 22), Harris-Benedict rev. (comparação); `SCALE_BMR_DEVIATION_WARN_SHARE` 0,15 | `mifflin_st_jeor`, `katch_mcardle`, `cunningham`, `select_formula`, `bmr(subject, formula) -> BmrResult`, `compare_with_scale` |
| `energy_expenditure.py` | TDEE, gasto do treino (natureza da kcal; a proveniência manual × Intervals é de `domain/calories_source.py`, L0) | `MET_*` (6,0/5,0/3,5/5,8), `MET_KCAL_FORMULA_FACTOR` 3,5, `MET_KCAL_DIVISOR` 200, `TRAINING_KCAL_NATURE_PRIORITY` (`reported`,`met`), `DEFAULT_STRENGTH_MET` 3,5. Keytel (`KEYTEL_MALE/FEMALE`, `heart_rate`, `KJ_PER_KCAL` para ele) (removido 2026-10-15, D-K) | `training_kcal_met`, `met_for_session`, `resolve_training_kcal(...) -> TrainingKcal(kcal, kcal_nature, nature, quality)`, `tdee(...) -> TdeeResult`. `training_kcal_keytel` (removido 2026-10-15, D-K) |
| `body_composition.py` | %G Marinha, MLG, FFMI, cintura/altura, peso-alvo | `NAVY_MALE/FEMALE`, `FFMI_*` (6,1; 1,8; 25), `WHTR_HEALTHY_MAX` 0,5, `BODY_FAT_METHOD_PRIORITY` (método da leitura; antes `BODY_FAT_SOURCE_PRIORITY`), `BODY_FAT_RANGE_VALID` (3, 60) | `navy_body_fat`, `ffm_kg`, `ffmi`, `ffmi_normalized`, `waist_to_height`, `target_weight_for_body_fat`, `resolve_body_fat -> BodyFat` |
| `energy_density.py` | densidade energética da variação de peso | Hall 2008: `FAT_TISSUE_KCAL_PER_KG` 9.400, `LEAN_TISSUE_KCAL_PER_KG` 1.800, `FORBES_CONSTANT_KG` 10,4 | `fat_share_of_weight_change`, `tissue_energy_density`, `daily_kcal_for_weekly_rate` |
| `strategy.py` | faixas/defaults por estratégia; alvo calórico | `CUT_*` (0,5–1,0 def. 0,7; 2,3–3,1 def. 2,6; 0,15–0,30 def. 0,25; déficit máx 0,25; piso 1,0×TMB), `BULK_*` (0,10–0,20 / 0,05–0,10; 0,25–0,5; 1,6–2,2 def. 2,0; 0,5–1,5; carbo mín 3–5), `MAINT_TOLERANCE_SHARE` 0,05 | `Strategy`, `StrategyParams.defaults_for(strategy, advanced)`, `kcal_target(...) -> KcalTarget(kcal, delta, rate_applied, clamped_by, limitations)` |
| `macros.py` | gramas/dia, proteína por refeição | `KCAL_PER_G_*` 4/4/9/7, `FIBER_G_PER_1000_KCAL` 14, `PROTEIN_PER_MEAL_G_PER_KG_RANGE` (0,40; 0,55), `MEALS_PER_DAY_RANGE` (3, 6) | `macro_split -> MacroTargets`, `protein_per_meal`, `kcal_from_macros` |
| `carb_cycling.py` | distribuição semanal com média preservada | `HIGH_CARB_MULTIPLIER` 1,25, `LOW_CARB_MULTIPLIER` 0,75, `MIN_LOW_CARB_G_PER_KG` 1,0 | `day_kinds_for_week`, `distribute_week` (invariante: soma = 7×alvo; proteína constante), `apply_cycle` |
| `phases.py` | calendário bulking/cutting/pausas/refeeds | `MATADOR_ON/OFF_WEEKS` 2/2, `ICECAP_ON/OFF_WEEKS` 3/1, `REFEED_DAYS_PER_WEEK_DEFAULT` 2, `MAX_CUT_WEEKS_WITHOUT_BREAK` 8, `BULK_STOP_BF`/`CUT_STOP_BF` (D-C) | `propose_calendar -> list[PhaseBlock]`, `phase_for_date`, `refeed_dates` |
| `adaptive_tdee.py` | gasto observado | `MIN_WINDOW_DAYS` 14, `PREFERRED_WINDOW_DAYS` 28, `MIN_LOGGED_DAYS_SHARE` 0,8, `MIN_WEIGHT_READINGS` 2, `MIN_WEIGHT_SPAN_DAYS` 7, `RECONCILE_WEIGHT_BY_QUALITY`. Tendência do peso por mínimos quadrados; `EMA_ALPHA` 0,3 (removido 2026-10-16: subestimava a variação, ver mapa §45) | `observed_tdee -> AdaptiveTdee(kcal, ESTIMATED, quality, limitations)`, `weight_trend`, `reconcile` |
| `weekly_budget.py` | orçamento, banco, aderência | `MAX_DAILY_CARRYOVER_SHARE` 0,10, `MAX_WEEKLY_BANK_SHARE` 0,05, `ADHERENCE_KCAL_TOLERANCE_SHARE` 0,05, `ADHERENCE_PROTEIN_MIN_SHARE` 0,90, `MIN_LOGGED_DAYS_FOR_WEEK` 5 | `weekly_budget`, `bank_state`, `weekly_adherence -> Adherence` |
| `meal_math.py` | macros de item/refeição, unidades, consistência | `GENERIC_PORTIONS` (colher sopa 15 ml, chá 5, xícara 240…), `ATWATER_TOLERANCE_SHARE` 0,15, `LABEL_SERVING_TOLERANCE_SHARE` 0,10 | `resolve_grams`, `item_macros`, `sum_macros`, `meal_nature`, `atwater_check`, `label_consistency` |
| `food_text.py` | normalização de nome e matching | `MATCH_MIN_SCORE` 0,6, `MATCH_AUTO_ACCEPT_SCORE` 0,85, `SYNONYMS` pt-BR | `normalize_name`, `score`, `rank_candidates` |
| `labels.py` | rótulos pt-BR e vocabulário RDC 429 | `STRATEGY_LABELS`, `DAY_KIND_LABELS`, `MEAL_SLOT_LABELS`, `ACTIVITY_LEVEL_LABELS`, `BMR_FORMULA_LABELS`, `RDC429_FIELDS` | — |
| `__init__.py` | agregador | `ENGINE_VERSION` | `export_for_frontend()` (bloco `nutrition`: rótulos, códigos, faixas de formulário, `rdc429_fields`, `generic_portions`, `meal_slots`) |

**Guarda** (allowlists nascem vazias; regex genérica leva `escopo` = pastas de nutrição; ajustar as regex ao abrir o L1 contra o código real):
| chave | ideia da regex | dono | ling. |
|---|---|---|---|
| `tmb_formula` | `370\s*\+\s*21[.,]6`, `500\s*\+\s*22\b`, `6[.,]25\s*[*×]`, `88[.,]362`, `447[.,]593` | `bmr.py` | py, ts, md |
| `fator_atividade` | `\b1[.,](375\|55\|725)\b` (escopo nutrição) | `anthropometrics.py` | py, ts |
| `kcal_por_grama` | `[*×]\s*[49](\.0)?\b` (escopo nutrição) | `macros.py`, `meal_math.py` | py, ts |
| `met_compendium` | `3[.,]5\s*/\s*200`, `/\s*200\b` (escopo nutrição + `workout_sessions.py`) | `energy_expenditure.py` | py, ts |
| ~~`keytel`~~ | (removido 2026-10-15, D-K) | — | — |
| `navy_gordura` | `86[.,]010\|70[.,]041\|163[.,]205\|97[.,]684` | `body_composition.py` | py, ts, md |
| `densidade_energetica` | `\b7[.,]?700\b\|\b9[.,]?400\b` | `energy_density.py` | py, ts, md |
| `faixa_proteina` | `\b[0-3][.,]\d\s*g\s*/\s*kg` (escopo nutrição + `rag/markdown/NUTRICAO/`; HYP-031/032 entram na allowlist inicial se tiverem g/kg) | `strategy.py`, `macros.py` | py, ts, md |
| `ffmi_normalizado` | `6[.,]1\s*[*×]\s*\(\s*1[.,]8` | `body_composition.py` | py, ts, md |
| `pausa_dieta` | `2 sem … 2` / `3\s*\+\s*1` (escopo nutrição + md) | `phases.py` | py, ts, md |
| `rotulo_estrategia` | `['"](Bulking\|Cutting\|Manutenção\|Dia alto em carbo)['"]` fora do JSON gerado | `labels.py` | ts |
| `kcal_treino_prioridade` | `calories_kcal\s*(or\|\?\?\|\|\|)` fora do dono | `energy_expenditure.py` | py, ts |

São 11 padrões no L1 (sem `keytel`). As regex finais ajustadas ao código, os escopos e a allowlist inicial estão no mapa (`FONTES_DA_VERDADE.md` §5 e §45). A allowlist tem 2 achados de `rotulo_estrategia` da intenção da metodologia de volume.

Consumo: SQL lê só tabelas materializadas; `nutrition_service.py` chama `resolve_training_kcal`, `observed_tdee`, `weekly_adherence`; prompts interpolam `labels.RDC429_FIELDS`, `GENERIC_PORTIONS`, `MEAL_SLOT_LABELS`; FE lê `motor-constants.generated.json.nutrition` via `frontend/src/lib/nutrition-constants.ts` + paridade vitest; `metrics_registry.THRESHOLDS` reexporta. Mapa: seção **§3.7 Nutrição** em `FONTES_DA_VERDADE.md` + linhas no §5 e §7. RAG: `rag/markdown/NUTRICAO/*.md` com seletor próprio (não entra nos prompts de treino).

## 8. API

### 8.1 api-core (`api/app/nutrition_*.py`, `include_router` em `main.py`)
Módulos: `nutrition_access.py` (`resolve_target_user(identity, user_id)`: próprio → ok; terceiro → `require_admin_or_pt`), `nutrition_schema.py` (503 citando a migração, padrão `body_measurements_schema.py`), `nutrition_plans.py`, `nutrition_targets_service.py` (`ensure_daily_targets(conn, user_id, date_from, date_to)` idempotente: lê usuário/bioimpedância/medidas/plano/atribuição de Modelo → `compute_daily_target` → upsert só de dias não congelados), `nutrition_foods.py`, `nutrition_meals.py`, `nutrition_days.py`, `nutrition_ai_jobs.py`, `nutrition_ai_quota.py`, `external_food_sources.py` (USDA/OFF/adaptador TBCA).

| Método/Path | Papel | Entrada → Saída | Erros |
|---|---|---|---|
| `GET /nutrition/plans?user_id=` | admin/PT | versões (vigente, histórico, rascunhos) | 403 |
| `POST /nutrition/plans` | admin/PT | `NutritionPlanIn` → rascunho | 400 faixa (códigos do motor), 404 usuário |
| `PUT /nutrition/plans/{id}` | admin/PT | só `draft` | 409 |
| `POST /nutrition/plans/{id}/preview` | admin/PT | `{subject, bmr, tdee, kcal_target, macros, week[7], phase_calendar, limitations}` sem gravar | 422 lista dados faltantes (altura, sexo, peso) |
| `POST /nutrition/plans/{id}/publish` | admin/PT | `{expected_current_id}` (a vigente que a tela viu, ou null; obrigatório) → supersede da vigente lida com `FOR UPDATE`; materializa a `publication_window` do motor (cria até o fim da semana, atualiza toda meta já materializada de hoje em diante; passadas intactas); `nutrition_phase_events` propostos no L8 | 409 `publish_conflict` (vigente mudou ou corrida no UNIQUE), 409 `plan_not_draft`, 422 dados faltantes |
| `DELETE /nutrition/plans/{id}` | admin/PT | só rascunho | 409 |
| `POST /nutrition/targets/recompute` | admin/PT | `{user_id, date_from, date_to?}` na `recompute_window` do motor (a partir do primeiro dia não congelado, ontem; fim padrão no fim da semana; até `TARGET_RECOMPUTE_MAX_DAYS`) | 400 com o código do motor (`date_from_frozen`, `date_to_before_date_from`, `range_too_long`), 404 `no_published_plan` |
| `GET /nutrition/me/plan` | usuário | plano em vigor hoje (`plan_in_force`) read-only + resumo do sujeito + meta de composição + metas da semana (materializa os dias não congelados) | 404 `no_published_plan` |
| `GET /nutrition/days/{date}?user_id=` | usuário / admin-PT | meta pela decisão do motor (`daily_target.diary_target_action`: materializa o não congelado; congelado SEM meta é calculado e gravado uma vez com `target_backfilled`; congelado com meta só lê; além da semana não cria), `target_action`, `target_notices`, refeições (com itens), totais do dia (`incomplete_fields`), `day_log` (água/fome), `training` (sessões com a proveniência, `comparison` informada × MET) e `balance` (por nutriente: meta, ingestão, completa?, saldo, fração; ingestão − TDEE) | 403 terceiro; 404 aluno; 503 082/083 |
| `PUT /nutrition/days/{date}?user_id=` | idem | `NutritionDayIn` (`water_ml`, `hunger`, `notes`): substitui a linha do dia | 400 `invalid_day_log` (`invalid_water_ml`, `invalid_hunger`, faixas de `diary`) |
| `GET /nutrition/weeks?start=&user_id=` | idem | a semana (segunda a domingo) que contém `start` (padrão: hoje do aluno): `days[7]` (meta, `target_action`, totais, `logged`, `intake_incomplete`) e `report` (`weekly_budget.week_report`: budget, bank, adherence, dias registrados e incompletos) | 403; 503 |
| `POST /nutrition/meals` · `PUT/DELETE /nutrition/meals/{id}` · `POST /nutrition/meals/preview` | idem | `MealIn` (`user_id?`, `local_date`, `meal_slot`, `eaten_at?`, `notes?`, `items[{id?, food_id, quantity, unit, portion_id?}]`) → nutrientes do motor congelados por item + `catalog_snapshot_json`; `PUT` (`MealBodyIn`) substitui os itens e o item com `id` e o mesmo alimento reusa o snapshot (E4); preview (`MealPreviewIn`, `meal_id?` na edição) não grava | 400 `invalid_meal` (`unit_without_conversion`, `missing_quantity`, `item_amount_out_of_range`, `invalid_meal_slot`, `meal_without_items`, `too_many_meal_items`; com o índice do item); 404 `food_not_found`/`portion_not_found`/`meal_not_found`/`meal_item_not_found`; 403 terceiro |
| `POST /nutrition/meals/parse` | idem | `MealParseIn(text, local_date, slot)` → **202** `{job_id}` | 429 cota |
| `GET /nutrition/foods/search?q=&source=` · `GET /{id}` · `POST` · `PUT /{id}` · `POST /{id}/portions` | qualquer (próprio) / admin (público) | FULLTEXT + `food_text.score`; `FoodItemIn` RDC 429 | 400 Atwater fora da tolerância vira `warnings` |
| `GET /nutrition/foods/barcode/{ean}` (L3b, `nutrition_external_foods.py`) | qualquer | GTIN validado pelo motor (8/12/13/14 dígitos, verificador; forma canônica) → cache (`food_items` `source='off'`, `source_id` = GTIN) → limite do dia (linhas `off` do usuário no fuso dele) → OFF API v3.6 (`User-Agent` do app, só `OFF_PRODUCT_FIELDS`) → grava PÚBLICO `source=off` + `barcode` → item + `warnings` + `from_cache` | 400 `invalid_barcode`; 404 `off_not_found`; 422 `invalid_external_food`; 429 `off_daily_limit` (`OFF_LOOKUPS_PER_USER_DAY` 30); 502/503/504 `off_*`; 503 tabelas |
| `GET /nutrition/foods/usda/search?q=` (L3b) | qualquer | proxy de `POST /fdc/v1/foods/search` só Foundation e SR Legacy (25 por página) → `{query, total_hits, items[{fdc_id, name (inglês), data_type, category, basis, per_100, energy_origin}]}` | 503 `usda_not_configured` (sem chave); 502/503/504 `usda_*` |
| `POST /nutrition/foods/usda/{fdc_id}/import` (L3b) | qualquer | `GET /fdc/v1/food/{fdcId}` → motor → grava PÚBLICO `source=usda`, `source_id` = fdcId, `source_version`, citação; idempotente (`UNIQUE (source, source_id)`): **201** criado, **200** já existia (`created`) | 503 `usda_not_configured`; 404 `usda_not_found`; 422 `invalid_external_food` (tipo de dado fora, sem energia); 502/503/504 |
| `POST /nutrition/foods/label-photo` (L6, `nutrition_label_photo.py`) | qualquer | JSON `{image_base64}` (JPEG já reduzido pela tela, base64 sem padding, por causa do WAF; não multipart) ≤ 1,5 MB → cota → S3 `nutrition-labels/{user}/{uuid}.jpg` → job na plan-generator → **202** `{job_id, status, poll_timeout_seconds}`; o resultado (`GET /jobs/{id}`) é o formulário pré-preenchido; `GET .../label-photo/usage` dá a cota do dia | 400 vazia/base64 inválido, 413, 415 (antes da cota), 429, 503 (S3/despacho) |
| `PATCH /workout-sessions/{id}/meta` (existente) | dono | + `calories_kcal`, `calories_source` | |
| `GET /workout-sessions/{id}` (existente) | dono | + `calories_estimate{kcal, kcal_nature, nature, quality}` (na leitura) | |

### 8.2 Engines (`analytics_dashboard.py`, prefixo `/analytics/dashboard`, `{proxy+}` já roteia)
`GET /analytics/dashboard/nutrition?window=` → `intelligence/dashboard/nutrition_service.build_nutrition(conn, *, user_id, window)`: `energy_balance[]` por dia (intake `DERIVED/ESTIMATED/MIXED`, target `PLANNED`, expenditure `ESTIMATED`, balance), `macros[]` vs meta, `weeks[]` (budget, adherence), `weight_phase` (reusa `weight-ma-v1` + `nutrition_phase_events`), `protein_by_meal[]` (28 d), `adaptive_tdee`, `phase_calendar[]`, `logging_coverage`. Contratos `NutritionResponse(DashboardEnvelope)` + `*Out` em `contracts.py`; `METRIC_VERSIONS` `nutrition_intake`, `nutrition_target`, `training_kcal`, `adaptive_tdee`, `nutrition_adherence`, `phase_overlay`. Orçamento de 29 s: só agregações sobre tabelas materializadas (uma query por bloco).

### 8.3 Jobs de IA
| job_type | Lambda | Timeout LLM | Env do modelo | Cota/dia/usuário |
|---|---|---|---|---|
| `nutrition-meal-parse` | `musclefit-workout-ai` (60 s) | 50 | `OPENAI_NUTRITION_PARSE_MODEL` (`lambda_workout_ai.tf`) | 30 |
| `nutrition-label-ocr` | `musclefit-plan-generator` (180 s) | 90 | `OPENAI_NUTRITION_VISION_MODEL` (`lambda_ai_generators.tf`) | 10 |
| `nutrition-meal-photo` (L10) | `musclefit-plan-generator` | 90 | idem | 10 |
Registro obrigatório: `async_jobs.JOB_TYPE_LAMBDA` (teste M73), `openai_client.LLM_OUTPUT_OBSERVED_MAX_TOKENS` (3 features), `ai_usage_log` pendente na rota 202 e fechado pelo worker (sweep de pendentes > timeout + graça). Resultado DynamoDB ≤ 400 KB (parse ~2 KB; rótulo ~3 KB).

## 9. IA

**(a) Texto da refeição → itens + casamento (D2)**: `nutrition_meal_parse_ai.py` (`PROMPT_VERSION="nutrition-parse-v1"`; prefixo cacheável; unidades de `GENERIC_PORTIONS` e slots interpolados; texto livre em `<dados_do_usuario>`). Schema strict `{items:[{name, quantity, unit (enum: g, ml, unit, tbsp, tsp, cup, slice, piece), grams_estimate|null, preparation|null, confidence, estimated_per_100g{kcal,protein_g,carbs_g,fat_g,fiber_g}|null}], notes}`. Worker determinístico depois da IA: FULLTEXT top 5 → `food_text.rank_candidates`; ≥ 0,85 pré-seleciona; < 0,6 → `ESTIMATED` + `candidate_for_catalog`. FE confirma → `POST /nutrition/meals`.

**(b) Foto de rótulo → RDC 429** (feito no L6, mapa §56): FE `resizeImageForUpload(file, { maxEdge: LABEL_PHOTO.resize_max_edge_px, quality: LABEL_PHOTO.resize_quality, contentType: LABEL_PHOTO.content_type })` (`frontend/src/lib/image-resize.ts`, canvas; parâmetros do bloco `nutrition.label_photo` do JSON gerado, nunca escritos à mão) → JSON com a foto em base64 sem padding (não multipart, por causa do WAF) → S3 → 202. `openai_client.call_openai_structured_vision(prompt, images: list[ImageInput], model, schema, *, schema_name, timeout, feature, user_id, prompt_version)` monta `input=[{"role":"user","content":[{"type":"input_text",…},{"type":"input_image","image_url":"data:…;base64,…","detail":"high"}]}]`, reutiliza `_post`, `_request_body` (`store:false`), `_structured_payload`, log de uso; `call_openai_structured` intacta. `nutrition_label_ai.py` (`nutrition-label-v2`; interpola `RDC429_FIELDS`, a norma e a base por 100; sem conta nem conferência no prompt — a v1 pedia a auto-checagem por porção com `LABEL_SERVING_TOLERANCE_SHARE` e saiu na revisão do guardião); schema `{product_name, brand, barcode, basis, serving_size, household_measure, servings_per_package, per_100{…RDC…}, per_serving{…}, legibility (ok/partial/unreadable), not_significant[], warnings[]}`. Worker (plan-generator; IAM `s3:GetObject nutrition-labels/*`) revisa pelo motor (`label_photo.review_reading`: `atwater_check`, `label_consistency`, `per_reference_from_serving`, zero do "sem quantidade significativa"). FE mostra formulário pré-preenchido → `POST /nutrition/foods` (`source=label_photo`).

**(c) Foto da refeição (L10)**: `nutrition_meal_photo_ai.py` (`nutrition-photo-v1`), mesma infra; saída = schema de (a) com `grams_estimate` obrigatório; tudo `ESTIMATED` até casar; prefixo S3 `nutrition-meals/` (30 d).

**Evals/custo/retenção**: casos em `tests/eval/casos/nutrition_meal_parse.json`, `nutrition_label.json` (imagens ≤ 300 KB em `tests/eval/imagens/nutrition_labels/`); critérios novos em `criterios_llm.py` (`avaliar_parse_refeicao`, `avaliar_rotulo`) **sem tocar em `FORA_DO_ESCOPO`**; snapshot `test_nutrition_prompts_snapshot.py` (o do rótulo, L6: `test_nutrition_label_prompt_snapshot.py`, porque a entrada tem imagem). Custo (modelo "mini"): parse ≈ US$ 0,0005; rótulo ≈ US$ 0,002; total 1–3 usuários ≈ **US$ 1–2/mês** (×10 com modelo full). `store:false`; foto expira em 30 d; `description_raw` fica só no nosso banco.

**Fonte de kcal e FC do treino (decisão de 2026-10-15)**: o sync automático do Intervals.icu (`intervals_sync.py`, cron 05:45 BRT, janela padrão de 30 dias) já grava kcal, FC média e máxima em `activity_intervals_link` e preenche `activities` por `COALESCE`. Nada a fazer no relógio Zepp nem no agente `zepp`. Lacuna: 47 treinos de jul–ago/2026 anteriores ao sync ficaram sem link; cobri-los exige uma execução única do job com `days` ampliado (escrita em produção pelo job idempotente existente, só com autorização do usuário). Os 23 sem `started_at` não casam por horário e permanecem `ESTIMATED` (MET).

## 10. Frontend (export estático)
| Rota | Arquivos | Quem vê | Conteúdo |
|---|---|---|---|
| `/nutricao` | `(app)/nutricao/page.tsx` + `NutricaoClient.tsx`; `components/nutrition/`: `DayHeader` (data + seletor de aluno para admin/PT), `TargetVsIntakeCard`, `MealList`/`MealCard` (selo DERIVED/ESTIMATED), `MealEditor` (busca, quantidade+unidade, porções, preview via API), `MealParseModal` (`dispatchAndPollJob`), `TrainingKcalRow` (informado × estimado), `WaterHungerInputs`, `WeekStrip` | todos; admin/PT com `?user_id=` | diário |
| `/nutricao/alimentos` | `AlimentosClient.tsx`; `FoodSearch`, `FoodForm` (RDC 429), `BarcodeLookup`, `LabelPhotoUpload` (resize + upload + polling), `UsdaSearch`, `PortionsEditor`; rodapé de citação TACO/USDA/OFF | todos (próprios), admin (públicos) | catálogo |
| `/nutricao/plano` | `PlanoClient.tsx`; `UserPicker` (reusar o da atribuição de Modelos), `PlanForm` (faixas do JSON gerado), `PlanPreview` (sujeito, TMB/TDEE, 7 dias, calendário recharts), `PlanHistory`; regular vê o próprio read-only | admin/PT (`canManageTrainingTypes`) | plano versionado |
| `/dashboard/nutricao` | `dashboard/nutricao/page.tsx` + `DashboardNutricaoClient.tsx` + `NutricaoCharts.tsx` (`dynamic()`, `chartTheme.ts`): ingestão × gasto × meta por dia; macros vs meta; peso MA7 × fases (`ReferenceArea`); aderência semanal; proteína por refeição; TDEE fórmula × adaptativo | todos | aba nova |
| Registro de treino | `WorkoutDetailClient.tsx`: campo "kcal gastas" (+ fonte) via `PATCH /meta`; linha "estimativa do motor" | dono | |
| Modo Treino (finish) | `workout-session-finish.ts` + modal: kcal opcional | dono | |
| Perfil / Indicadores | altura, sexo canônico, nível de atividade (aviso "necessário para nutrição"); sites `neck`/`forearm_*` | dono | |
| Usuários | papel `personal_trainer` selecionável | admin | |
Infra FE: `lib/nutrition-api.ts`, `lib/nutrition-types.ts`, `lib/nutrition-constants.ts`, `dashboard-api.ts` + `fetchNutrition`/`dashboardKeys.nutrition`; `AppShellLayout.tsx`: `navItems` + `{ href:'/nutricao', label:'Nutrição', prefixes:['/nutricao'] }`, `DASHBOARD_NAV_ITEMS` + `/dashboard/nutricao`; espelho: `test_m76_lote1b_espelhos_fe.py --gravar` inclui `domain.nutrition.export_for_frontend()`; paridade vitest `nutrition-constants.parity.test.ts`; `smoke:export`.

## 11. Relatório Markdown e insights
- `markdown_report_service.py`: `SECTION_LABELS["nutrition"]`, `_collect_nutrition` (metas × ingestão, semanas, fases, TDEE adaptativo, cobertura), `_empty_sources`. `markdown_renderer.py`: seção **no fim** (numeração fixa preservada), nunca "0" para ausência.
- **Não muda**: `insights_prompt.py`, `criterios_llm.FORA_DO_ESCOPO`, `intelligence_package`. Se `report_ai.py` consumir a seção, só no L9 com critérios próprios (comparação meta × registrado, sem recomendação clínica).
- Insights determinísticos de nutrição (ex.: proteína < 90% em 3 dias) ficam em `nutrition_service`, pós-L9.

## 12. Infra
| Item | Arquivo | Mudança |
|---|---|---|
| Modelos | `variables.tf`, `lambda_workout_ai.tf`, `lambda_ai_generators.tf` | `OPENAI_NUTRITION_PARSE_MODEL` (workout-ai); `OPENAI_NUTRITION_VISION_MODEL` (plan-generator) |
| USDA | `secrets.tf`, `lambda_api_core.tf` | secret `musclefit/usda-api-key`, criado à mão pelo usuário em 2026-10-17 (com o valor) e **importado** no Terraform (`import {}`, sem `_version`; PR #562). Só a api-core recebe `USDA_API_KEY_SECRET_ARN`; `secrets_bootstrap.py` hidrata `USDA_API_KEY`, o mesmo nome que o código lê no DEV (`.env`) e na produção. Por isso as rotas do USDA do L3b ficam no app da api-core (`app/main.py`). O apply usa só permissões que a role de deploy já tem (`DescribeSecret`, `GetResourcePolicy`, `TagResource`): **sem exceção de IAM** |
| OFF | `lambda_api_core.tf` | `OFF_USER_AGENT = "MuscleFit/1.0 (<contato>)"` |
| S3 | `s3_uploads.tf` (lifecycle único) | regras `nutrition-labels-expire-30d` e (L10) `nutrition-meals-expire-30d`; conferir escopo do `Resource` da policy da api-core |
| IAM plan-generator | `lambda_ai_generators.tf` | `s3:GetObject` em `${bucket}/nutrition-labels/*` (hoje só `PutObject` em `exercise-card-drafts/*`) |
| Lambda nova | — | **nenhuma** (parse cabe nos 60 s; vision nos 180 s do plan-generator, que já tem chave, bucket e `lambda_async_invoke_config`) |
| Engines | — | 29 s: `build_nutrition` só agrega tabelas materializadas |

## 13. Lotes / PRs (todos para `develop`; ordem e paralelismo)
| Lote | Conteúdo | Tam. | Depende | Aceite | Impacto em produção |
|---|---|---|---|---|---|
| **L0 Fundação** | 074–076; `users` (altura, sexo, nível, PT em `_ALLOWED_ROLES`), `/auth/me`; `SessionMetaIn.calories_*` + tela do treino; sites novos + rótulos do JSON; apagar código morto | M | — | migrações 0 statements na reexecução; contrato estrito; PATCH grava kcal; `/auth/me` com 4 campos; cobertura kcal/FC do Intervals remedida após o reprocessamento único do histórico | ALTERs aditivos; ENUM no fim; promoção com RUN_DB_MIGRATIONS=true e dry-run |
| **L1 Motor + guarda + mapa** | `domain/nutrition/*`, testes parametrizados, 11 `Padrao` + ALLOWLIST, §3.7 do mapa, `export_for_frontend` → JSON + vitest | M | — (∥ L0) | tabelas de referência batem; guarda verde | nenhum |
| **L2a Perfil antropométrico** | 077 (`sex` canônico); `UserProfileUpdateIn` + `height_cm`/`activity_level`, com `sex`, validados por `anthropometrics` (400 com o código do motor), também no update do admin; `/auth/me` e listagens com o código canônico; `PerfilClient`/`UsuariosClient` com opções, rótulos e faixa de `nutrition-constants.ts`; guarda `vocabulario_sexo` | S/M | L0, L1 | 077 com 2 statements em produção e 0 na reexecução; contrato estrito; guarda verde com allowlist vazia | backfill de 2 linhas (2 NULL preservados); promoção com RUN_DB_MIGRATIONS=true e dry-run |
| **L2b Plano admin/PT** | 078, 079; `nutrition_plans.py`, `nutrition_targets_service.py`, `nutrition_access.py`, `nutrition_schema.py`; `/nutricao/plano`; `GET /nutrition/me/plan` | L | L0, L1 (∥ L2a) | preview sem gravar; publish supersede; metas congeladas; 422 lista faltantes | tabelas novas; router novo |
| **L3 Catálogo** | 080, 081 (TACO); `nutrition_foods.py`, `external_food_sources.py`; `/nutricao/alimentos` (sem foto); secret USDA + UA | L | L0 (∥ L2a, L2b) | busca < 300 ms; seed idempotente; OFF com cache; citações visíveis | seed em prod; secret manual |
| **L4 Diário** | 082, 083; `nutrition_meals.py`, `nutrition_days.py`, preview; `/nutricao` | L | L2b, L3 | macros congeladas = motor; `MIXED`; admin/PT vê aluno | tabelas novas; menu |
| **L5 IA texto** | 085; `nutrition_meal_parse_ai.py`, job type, dispatch, teto de tokens, cota, `MealParseModal`, eval + snapshot | M | L4 | eval paga ≥ limiar; 429; `ai_usage_log` fechado | env + TF apply |
| **L6 IA rótulo** | `call_openai_structured_vision`, `/nutrition/foods/label-photo`, lifecycle, IAM GetObject, dispatch plan-generator, `image-resize.ts`, `LabelPhotoUpload`, eval com imagens | M/L | L3, L5 (∥ se `ai_usage_log` sair em L5a) | foto 1,5 MB → < 30 s; strict+imagem confirmado | TF apply (lifecycle + IAM); env vision |
| **L7 Dashboard** | `nutrition_service.py`, contratos, registry, rota, `/dashboard/nutricao` | L | L4 (+L2b) | `extra="forbid"`; `None` nunca 0; < 5 s em 90 d | rota na engines; menu |
| **L8 Ciclos/fases/adaptativo** | 086; `carb_cycling`/`phases` na materialização (dias de treino/pernas via atribuição de Modelo); refeeds/pausas; `adaptive_tdee` no dashboard + sugestão de recálculo; `context_service` deriva fases | M | L7 | média semanal de carbo = alvo; adaptativo só com ≥ 14 d e ≥ 80% | tabela nova; metas futuras recomputadas |
| **L9 Relatório/insights** | seção Markdown; critérios do `report_ai` se aplicável | S/M | L7 | snapshot; `insights.json` inalterado | nenhum |
| **L10 Foto de refeição** (futuro) | `nutrition_meal_photo_ai.py`, job, lifecycle, FE | M | L6 | eval com fotos; tudo `ESTIMATED` até casar | TF apply |
| **L11 Hardening** (opcional) | painel de custo/cota, vínculo PT↔aluno, insights determinísticos | M | L7 | — | — |

Ajuste 2026-10-15 (L0): PATCH e telas de perfil (altura, sexo, nível de atividade) movidos para o L2 por dependerem do motor do L1; kcal no finish do Modo Treino retirado (o Intervals preenche automaticamente); a edição manual fica na tela do registro do treino.
Backfill de sex para male/female/other movido para o L2, junto com as telas de perfil e o vocabulário de anthropometrics.Sex (as telas atuais usam M/F).

Ajuste 2026-10-16: migrações do L0 renumeradas de 073–075 para 074–076 (a 073 é a do modo de contagem do volume, já em produção; o runner recusa versão duplicada); as seguintes andam uma casa (077–084).

Ajuste 2026-10-17: o L2 foi dividido em L2a (perfil antropométrico: migração 077, `users.sex` canônico, PATCH e telas de perfil/usuários) e L2b (plano admin/PT: 078–079). As migrações seguintes andam mais uma casa, e a série da nutrição passa a 074–085. No mapa dos motores, o L2a é a §46 e o L2b, a §47.

Ajuste 2026-10-17 (M85): a 084 passou a ser a dos campos "em reavaliação" da TACO com o USDA (`084_food_items_taco_usda_reavaliacao.py`, mapa §48.1); o L5 vai para a 085 e o L8 para a 086 (o runner recusa versão duplicada). A série da nutrição passa a 074–086.

Ajuste 2026-10-17 (L2b, revisão do #561): o DDL do §6.2 passou a usar os nomes do motor (`weekly_rate_share`, `advanced`, `carb_min_g_per_kg`, `published_by`, `training_kcal_mode`/`tdee_mode`, `tdee_kcal`, `training_kcal`, `training_kcal_nature`, `is_training_day`, `training_known`, `inputs_hash`; sem `data_quality`; `water_ml` NULL até existir regra). Janelas de materialização (publicação, recálculo, semana), primeiro dia recalculável e teto ficam no motor (`daily_target`); a publicação usa concorrência otimista (`expected_current_id` + `FOR UPDATE`) e a materialização lê os planos com `FOR SHARE`, pula o que não mudou (hash das entradas) e grava em lote. O sujeito do dia é escolhido por grandeza no motor (`subject.SubjectSeries`). Mapa: §47.

Ajuste 2026-10-17 (L3a, catálogo com a TACO): o L3 foi dividido em L3a (080, 081, `nutrition_foods.py`, motor
`catalog`, calibração do `food_text` e `/nutricao/alimentos` sem foto, USDA nem código de barras) e L3b (USDA, Open
Food Facts, secret e User-Agent). Desvios do DDL do §6.2, todos para não converter nome fora do dono: colunas por
100 g/ml com os códigos da RDC 429 do motor (`carbohydrates_g`, `total_fat_g`, `total_sugars_g`, `added_sugars_g`,
`saturated_fat_g`, `trans_fat_g`; porção do rótulo em `serving_size`, `household_measure`, `servings_per_package`);
`food_portions.amount` na base do alimento (g ou ml), não `grams`; `food_portions.owner_user_id` (porção do usuário
num alimento público é privada, D-G). TACO somente leitura pela API (409); `NA`, `*` e vazio da TACO viram NULL, `Tr`
vira 0; a planilha junta "Nozes e sementes" a "Leguminosas" e o seed segue os 15 grupos da publicação. Motor novo
`domain/nutrition/catalog.py` (fontes, citação, campos, regras de entrada) e guarda `catalogo_alimentos`. Mapa: §48.

Ajuste 2026-10-17 (L3b, USDA e Open Food Facts): sem migração; os itens das duas fontes gravam em `food_items` (080)
como PÚBLICOS e somente leitura, com `created_by`, citação e licença por linha. Rotas num módulo irmão incluído pela
api-core (`api/app/nutrition_external_foods.py`; só ela tem a chave do USDA) e cliente HTTP fino em
`api/app/external_food_sources.py` (`urllib`, timeout de 8 s, chave no cabeçalho `X-Api-Key`, erros 404/502/503/504).
Motor novo `domain/nutrition/external_sources.py`: ids de nutriente do USDA, chaves do OFF, conversões (kJ → kcal,
sódio g → mg), escolha da energia, GTIN e o limite diário; citações/licenças e as fontes somente leitura em
`catalog`. Desvios do §4/§8.1: (a) a API v3.6 do OFF não devolve mais as chaves planas `*_100g` (`nutriments`); o
motor lê `nutrition.aggregated_set` (base pelo `per`: 100g ou 100ml), conferido no payload real; (b) valores que o OFF
ESTIMA a partir dos ingredientes e o `value_computed` não entram; (c) na importação só a energia é obrigatória
(`catalog.EXTERNAL_REQUIRED_PER_100_FIELDS`), o resto ausente fica NULL; (d) o `OFF_USER_AGENT` do §12 virou constante
no código (`MuscleFit/1.0 (+https://musclefit.com.br)`, sem e-mail), sem variável de ambiente nem infra. Limitação: o
limite diário do OFF conta as linhas gravadas, então consulta sem resultado (404) não conta. Guarda
`fontes_externas_alimentos`. Mapa: §49.

Ajuste 2026-10-17 (L4, diário de refeições): migrações 082 (`meals` + `meal_items`) e 083 (`nutrition_days`);
`nutrition_meals.py` e `nutrition_days.py` (rotas reais no §8.1); tela `/nutricao` (diário; o menu "Nutrição" passa a
abri-la; sem `MealParseModal`, que é do L5). Desvios do DDL do §6.2, para não converter nome fora do dono: nutrientes
com os códigos da RDC 429 (`energy_kcal`, `protein_g`, `carbohydrates_g`, `total_fat_g`, `fiber_g`, `sodium_mg`, como a
080); `amount` + `basis` (quantidade na base do alimento, g ou ml) no lugar de `grams_resolved`; porção cadastrada por
`portion_id`; `totals_incomplete_json`, `item_count`, `limitations_json` e `nature` por item. Motor: `meal_math` (regra
do macro ausente: zero só quando os macros conhecidos explicam a energia; fibra zero com carboidrato zero; o resto
desconhecido e o total com o campo incompleto), módulo novo `diary` (refeição, entrada, água e fome), `weekly_budget`
(dia registrado só com total completo, balanço do dia, relatório da semana), `daily_target` (decisão do diário por dia
e meta retroativa, R8) e `energy_expenditure.compare_training_kcal`. Guarda: `diario_nutricao` e `soma_nutrientes`.
Mapa: §52.

Ajuste 2026-10-18 (L5a, base de uso e cota de IA): o L5 foi dividido em L5a (migração 085 `ai_usage_log`, motor
`domain/nutrition/ai_usage.py` e serviço `api/app/nutrition_ai_quota.py`, sem rota, tela nem IA) e L5b (IA no texto
da refeição), para o L5b e o L6 rodarem em paralelo sobre a mesma base. Features `meal_parse` (L5b) e `label_photo`
(L6), com os job types do §8.3 como constantes do motor; cota 30 e 10 por dia do usuário contando toda tentativa;
sweep dos pendentes no worker, a cada fechamento, sem agendamento nem infra, pelo prazo do worker perdido do M73
(`async_jobs.worker_lost_deadline_seconds`, que vale para a feature assim que o L5b/L6 registrar o job type em
`JOB_TYPE_LAMBDA`); custo pela tabela de preços com fonte (relatório de 2026-10-08), NULL para modelo sem preço.
Desvios do DDL do §6.2: `error_code` e `updated_at` a mais; `started_at` é o `created_at` do §6.1; `cost_usd`
DECIMAL(10,6). Guardas `cota_ia_nutricao` e `cota_ia_copia_nutricao`. Mapa: §54 (o L5b é a §55 e o L6, a §56).

Ajuste 2026-10-18 (L5b, IA no texto da refeição): sem migração (a 082 já tinha `description_raw`, `ai_job_id`,
`source = ai_text`, `meal_items.food_id` NULL e `match_confidence`). Motor novo `domain/nutrition/meal_parse.py`
(vocabulário da IA, estimativa por 100 g, decisão do casamento, quantidade proposta e item estimado no diário) e a
regra da origem do item em `diary`; rota `api/app/nutrition_meal_parse.py` (`POST /nutrition/meals/parse` 202 e
`GET /nutrition/meals/parse/quota`), prompt e schema em `nutrition_meal_parse_ai.py`, worker em
`nutrition_meal_parse_worker.py` (Lambda `musclefit-workout-ai`); `POST /nutrition/meals` aceita o item estimado
(sem `food_id`, com a estimativa) e grava `ai_text`. Desvios do §9 (a): unidades de `meal_math.Unit` (sem
`slice`/`piece`) e campos da RDC 429 na estimativa (sem `kcal`/`carbs_g`/`fat_g`); candidatos = os 5 melhores pela
nota dentro do lote do FULLTEXT (a busca do L3a), não os 5 primeiros do FULLTEXT; duas buscas por item (com e sem o
preparo); quantidade proposta = os gramas totais da IA (a medida em volume não converte pela densidade 1 g/ml que o
diário assumiria). A cota é de quem pede (o admin/PT gasta a dele). A variável do modelo ficou em
`infra/lambda_workout_ai.tf`, com as irmãs da Lambda, e não em `variables.tf`. Casos do eval em
`tests/eval/casos/meal_parse.json` (o nome da feature, que o framework das evals exige), não
`nutrition_meal_parse.json`; o snapshot do prompt está no snapshot comum das features e
`test_nutrition_prompts_snapshot.py` prova que todo número do prompt vem da fonte. Eval pago: 9 de 9 casos, 27 de 27
itens com proposta aceita (23 casados com a TACO), 0 pré-seleção errada, ≈ US$ 0,002 por leitura. Mapa: §55.

Ajuste 2026-10-18 (L6, IA na foto do rótulo): sem migração (o alimento vai para `food_items` com
`source = label_photo`; `label_s3_key` da 080 fica NULL, porque a foto expira em 30 dias e o alimento não depende
dela). Motor novo `domain/nutrition/label_photo.py` (limite de 1,5 MiB, só JPEG, chave e retenção no S3, redução
1280 px / 85%, revisão da leitura: base, kJ → kcal, por 100 derivado da porção, regras do `catalog` com Atwater e
porção × por 100 do `meal_math`); a IA só transcreve. Rota `nutrition_label_photo.py` (corpo JSON com a foto em base64 sem padding, não multipart, para o CRS do WAF não
ler bytes crus de JPEG; 413/415/400 antes da cota),
armazenamento `nutrition_label_storage.py`, worker `nutrition_label_ai.py` na `musclefit-plan-generator`,
`openai_client.call_openai_structured_vision` (D-E confirmada: strict + `input_image`), prompt `nutrition-label-v2`
(sem conta nem conferência; "sem quantidade significativa" vira zero no motor), tela `LabelPhotoUpload` em
`/nutricao/alimentos` com `frontend/src/lib/image-resize.ts` (a foto é SEMPRE reencodada por canvas antes do envio;
falhou, nada é enviado — por causa do WAF do #579). O `POST /nutrition/foods` passou a aceitar `source`
(`catalog.USER_ENTRY_SOURCES`). Infra: regra `nutrition-labels-expire-30d`, `s3:GetObject` em `nutrition-labels/*` para
a role da plan-generator e `OPENAI_NUTRITION_VISION_MODEL` (variável em `lambda_ai_generators.tf`, junto dos modelos
da plan-generator, e não em `variables.tf`). Eval com imagens sintéticas (fonte Inter, OFL): v1 16/16 e v2 16/16, ≈ US$ 0,11 nas 4 rodadas. Guardas
`foto_rotulo_nutricao` e `foto_rotulo_numeros`. Mapa: §56.

Paralelismo (worktrees isolados): L0 ∥ L1; L2a ∥ L2b ∥ L3; L5 ∥ L6 (com L5a); L8/L9 após L7.

## 14. Testes
| Tipo | Arquivos | Cobre |
|---|---|---|
| Motor | `test_nutrition_anthropometrics.py`, `_bmr.py`, `_energy_expenditure.py`, `_body_composition.py`, `_energy_density.py`, `_strategy_macros.py`, `_carb_cycling.py`, `_phases.py`, `_adaptive_tdee.py`, `_weekly_budget.py`, `_meal_math.py`, `_food_text.py`, `_daily_target.py`, `_labels.py` | `parametrize` com valores das fontes (Mifflin H 80 kg/180 cm/30 a → 1.780, com 35 a dá 1.755; Katch MLG 65 → 1.774; Cunningham → 1.930; MET 3,5×80×60 min = 294; Keytel (removido 2026-10-15, D-K); Navy; FFMI; Hall FM 15 kg); invariantes (soma semanal, proteína constante, `None` sem altura/sexo, `OTHER` sem MLG → `None`) |
| Guarda | `test_guarda_fontes_da_verdade.py` | 11 `Padrao` (sem `keytel`, D-K); allowlist inicial documentada |
| Espelhos FE | `test_m76_lote1b_espelhos_fe.py` + `nutrition-constants.parity.test.ts` | bloco `nutrition` |
| Contrato | `test_contrato_frontend_modelos_estritos.py` | payloads novos (`extra="forbid"`) |
| Migrações | `test_migration_074..086.py` (FakeDB) | ausente → N; convergido → 0; divergente → erro; ENUM 075 = `MEASUREMENT_SITES` |
| Materialização | `test_nutrition_targets_service.py` | congelamento, supersede, 422 |
| Prompts | `test_nutrition_prompts_snapshot.py` (L6: `test_nutrition_label_prompt_snapshot.py` + `test_nutrition_label_ai.test_prompt_so_tem_numero_interpolado_do_dono`) | renderizado; sem dígito fora das constantes |
| Jobs | `test_m73_async_jobs_worker_lost.py` (+3 tipos), `test_nutrition_ai_quota.py` | mapa job→Lambda; 429; sweep |
| Eval paga | `casos/nutrition_meal_parse.json`, `nutrition_label.json` | só com chave |
| Integração | `integration/test_nutrition_flow.py` | plano → publish → dia → refeição → dashboard |
| Dashboard | `test_dashboard_nutrition_service.py` | `None` nunca 0; qualidade; fuso |
| Export | `npm run smoke:export` | rotas novas |

## 15. Riscos e decisões abertas
| ID | Questão | Padrão proposto |
|---|---|---|
| D-A | Fatores de não-exercício (1,2/1,3/1,4/1,5/1,6) sem fonte canônica única | adotar; TDEE adaptativo sempre ao lado; PT ajusta |
| D-B | Piso no cutting (≥ 1,0×TMB; déficit ≤ 25%) | adotar; `clamped_by` avisa |
| D-C | %G de parada (H 15/10, M 25/18) | default editável |
| D-D | `sex` in-place (na 077, L2a, com as telas de perfil; saiu da 074) | sim, com `SELECT DISTINCT` antes (o `plan` da 077 lê os valores exatos e loga o que sobra fora do vocabulário) |
| D-E | strict JSON + `input_image` | confirmar no L6; fallback `json_object` |
| D-F | TACO como seed por migração | sim (idempotente, versionada, dev = prod) |
| D-G | Alimento criado por usuário é privado | sim; admin promove |
| D-H | Semana na segunda; banco 10%/dia, 5%/sem | constantes do dono |
| D-I | Ciclo de carbo: kcal acompanha o carbo | sim (média semanal preservada) |
| D-J | Adaptativo recalcula sozinho? | não, só sugere |
| D-K | Keytel em força | **decidido**: Keytel removido em 2026-10-15 (D-K); o L1 implementou só kcal observada → MET (`ESTIMATED`). Motivo: o Intervals entrega kcal em 150/150 atividades `WeightTraining`, logo "FC sem kcal" não ocorre |
| D-L | PT sem vínculo vê qualquer aluno (D1) | manter; `nutrition_access` centraliza |
| R1 | FULLTEXT no Aurora via Data API | índice na 080 antes do seed 081 |
| R2 | `ALTER ENUM` da 075 não ser instant | dry-run em produção antes da promoção; MODIFY só de metadado no MySQL 8, com precedente pela Data API nas migrações 023, 025, 027 e 028 |
| R3 | 29 s da engines com 180 d | agregar por dia no SQL; `protein_by_meal` 28 d |
| R4 | ODbL exige atribuição | `citation` por linha + rodapé. **Feito no L3b (2026-10-17)**: cada linha `off` leva o aviso da ODbL (seção 4.3: "Contém informações do Open Food Facts (https://world.openfoodfacts.org), disponibilizadas sob a Open Database License (ODbL).") + o link do produto, e a licença (ODbL v1.0; conteúdo DbCL v1.0); cada linha `usda` leva a citação sugerida pelo FDC + tipo e FDC ID, licença CC0 1.0; o rodapé de `/nutricao/alimentos` mostra citação e licença de TACO, USDA e OFF (textos de `catalog`, via JSON gerado) |
| R5 | TACO legível por máquina não localizada oficialmente | **resolvido no L3a (2026-10-17)**: planilha de terceiros entregue pelo usuário (`docs/nutricao/fontes/taco_4a_edicao.xlsx`, SHA-256 no README de fontes) conferida contra o PDF oficial do NEPA (baixado de nepa.unicamp.br, SHA-256 registrado) — não por amostra, a tabela inteira: 597 de 597 alimentos em energia, proteína, lipídeos, carboidrato e fibra (578 automáticos, 2.890 de 2.890 valores; 19 com célula em branco no PDF conferidos à mão), sódio em 575 de 575 conferidos, 15 grupos; citação da 4ª ed. em cada linha e no rodapé da tela |
| R6 | Tamanho de statement da Data API no seed | blocos ≤ 50 linhas; dados embutidos no módulo (checksum) |
| R7 | Secret do USDA (resolvido em 2026-10-17) | o usuário criou `musclefit/usda-api-key` e o Terraform o importa (PR #562); o apply usa só `DescribeSecret`, `GetResourcePolicy` e `TagResource`, que a role de deploy já tem; sem ampliação de role nem exceção de IAM |
| R8 | Metas diárias dependem de alguém abrir a tela (L2b não tem job): um dia só é (re)calculado enquanto não congelou e alguém publica, recalcula ou abre `GET /nutrition/me/plan`; se ninguém abrir, o dia congela sem meta ou com a meta de antes da kcal do Intervals | **resolvido no L4 (2026-10-17)**, no motor (`daily_target.diary_target_action`): o diário (`GET /nutrition/days/{date}` e `/nutrition/weeks`) materializa o dia não congelado pelo fluxo do L2b (abrir o diário hoje ou ontem atualiza a kcal do Intervals) e, para o dia que congelou SEM meta, calcula com os dados disponíveis e grava UMA vez (`INSERT ... ON DUPLICATE KEY UPDATE id = id`), marcado em `limitations_json` (`target_backfilled`) e `inputs_json.materialization`; a 079 não mudou e não houve migração nova (a 084 é do M85). Congelado com meta continua intocado. Sem job nem infra. Resta: o dia congelado COM a meta de antes da kcal do Intervals (ninguém abriu ontem) fica como está |

## 16. Fontes consultadas (2026-10-14)

Fórmulas e recomendações:
- Frankenfield D. et al. Comparison of predictive equations for resting metabolic rate in healthy nonobese and obese adults: a systematic review. J Am Diet Assoc 2005. https://pubmed.ncbi.nlm.nih.gov/15883556/
- Mifflin M.D. et al. A new predictive equation for resting energy expenditure in healthy individuals. Am J Clin Nutr 1990;51:241-247.
- Cunningham J.J. A reanalysis of the factors influencing basal metabolic rate in normal adults. Am J Clin Nutr 1980;33:2372-2374. Katch-McArdle: McArdle, Katch & Katch, Exercise Physiology (370 + 21,6 × MLG).
- Helms E.R., Aragon A.A., Fitschen P.J. Evidence-based recommendations for natural bodybuilding contest preparation: nutrition and supplementation. JISSN 2014;11:20. https://pmc.ncbi.nlm.nih.gov/articles/PMC4033492/
- Iraki J. et al. Nutrition Recommendations for Bodybuilders in the Off-Season: A Narrative Review. Sports 2019;7(7):154. https://pmc.ncbi.nlm.nih.gov/articles/PMC6680710/
- Slater G.J. et al. Is an Energy Surplus Required to Maximize Skeletal Muscle Hypertrophy Associated With Resistance Training. Front Nutr 2019. https://www.frontiersin.org/journals/nutrition/articles/10.3389/fnut.2019.00131/pdf
- Morton R.W. et al. A systematic review, meta-analysis and meta-regression of the effect of protein supplementation on resistance training-induced gains in muscle mass and strength. BJSM 2018;52:376-384. https://bjsm.bmj.com/content/52/6/376.abstract
- Garthe I. et al. Effect of two different weight-loss rates on body composition and strength and power-related performance in elite athletes. IJSNEM 2011;21(2):97-104. https://www.sponet.de/sponet/Record/4020931
- Aragon A.A. et al. ISSN position stand: diets and body composition. JISSN 2017;14:16. https://pmc.ncbi.nlm.nih.gov/articles/PMC5470183/
- Byrne N.M. et al. Intermittent energy restriction improves weight loss efficiency in obese men: the MATADOR study. Int J Obes 2018;42:129-138. https://pmc.ncbi.nlm.nih.gov/articles/PMC5803575
- Peos J.J. et al. Continuous versus Intermittent Dieting for Fat Loss and Fat-Free Mass Retention in Resistance-trained Adults: The ICECAP Trial. MSSE 2021. https://pubmed.ncbi.nlm.nih.gov/33587549/ (análise secundária: https://www.ncbi.nlm.nih.gov/pmc/articles/PMC7906362/)
- Campbell B.I. et al. Intermittent Energy Restriction Attenuates the Loss of Fat Free Mass in Resistance Trained Individuals. A Randomized Controlled Trial. JFMK 2020;5(1):19. https://www.mdpi.com/2411-5142/5/1/19
- Hall K.D. What is the required energy deficit per unit weight loss? Int J Obes 2008;32:573-576. https://pubmed.ncbi.nlm.nih.gov/17848938/ (densidade energética por composição; Forbes 1987 para a curva MLG × gordura)
- Hodgdon J.A., Beckett M.B. Prediction of percent body fat for U.S. Navy men/women from body circumferences and height. NHRC 1984. https://apps.dtic.mil/sti/pdfs/ADA143890.pdf
- Kouri E.M. et al. Fat-free mass index in users and nonusers of anabolic-androgenic steroids. Clin J Sport Med 1995;5(4):223-228 (FFMI normalizado; referência 25 com ressalvas).
- Keytel L.R. et al. Prediction of energy expenditure from heart rate monitoring during submaximal exercise. J Sports Sci 2005;23(3):289-297. https://pubmed.ncbi.nlm.nih.gov/15966347/ (coeficientes a conferir no texto integral antes do L1)
- Herrmann S.D. et al. 2024 Adult Compendium of Physical Activities. J Sport Health Sci 2024. https://pmc.ncbi.nlm.nih.gov/articles/PMC10818145/ ; tabela de condicionamento: https://pacompendium.com/conditioning-exercise/
- Razão cintura/altura < 0,5 (Ashwell & Hsieh 2005): limiar amplamente usado; **não verificado na fonte primária nesta sessão**.

Tabelas de alimentos, rotulagem e APIs:
- TBCA — Tabela Brasileira de Composição de Alimentos, USP/FoRC, v7.3 (2025). https://www.tbca.net.br/ (termos de uso e contato na página inicial)
- TACO — Tabela Brasileira de Composição de Alimentos, NEPA/UNICAMP, 4ª ed. rev. ampl., 2011. PDF: https://edisciplinas.usp.br/mod/resource/view.php?id=2301180 ; NEPA: www.unicamp.br/nepa
- USDA FoodData Central — guia da API: https://fdc.nal.usda.gov/api-guide ; downloads: https://fdc.nal.usda.gov/download-datasets/ (CC0 1.0)
- Open Food Facts — documentação da API (v3.6, limites, User-Agent, ODbL/DbCL): https://openfoodfacts.github.io/documentation/docs/Product-Opener/api/
- ANVISA — RDC nº 429/2020 e IN nº 75/2020 (rotulagem nutricional; vigência desde 2022-10-09; tabela por 100 g/ml e por porção, %VD).
- OpenAI — Images and vision (Responses API, `input_image`, `detail`, tokens por patch): https://developers.openai.com/api/docs/guides/images-vision

## 17. Estado de execução

| Lote | Estado | PR | Data |
|---|---|---|---|
| L0 Fundação | em produção (#571) | #556 | 2026-10-16 |
| L1 Motor + guarda + mapa | em produção (#571) | #557 | 2026-10-16 |
| L2a Perfil antropométrico | em produção (#571) | #560 | 2026-10-17 |
| L2b Plano admin/PT | em produção (#571) | #561 | 2026-10-17 |
| L3a Catálogo + TACO | em produção (#571) | #563 | 2026-10-17 |
| L3b USDA + Open Food Facts | em produção (#571) | #565 | 2026-10-17 |
| L4 Diário | em produção (#571) | #568 | 2026-10-17 |
| M85 TACO em reavaliação → USDA (migração 084) | na develop | #577 | 2026-10-18 |
| L5a Base de uso e cota de IA (migração 085) | na develop | #580 | 2026-10-18 |
| L5b IA texto (sem migração; env do modelo na workout-ai) | na develop | #581 | 2026-10-18 |
| L6 IA rótulo (sem migração; env do modelo na plan-generator) | em revisão (este PR) | — | 2026-10-18 |
| L7 Dashboard | não iniciado | — | — |
| L8 Ciclos/fases/adaptativo | não iniciado | — | — |
| L9 Relatório/insights | não iniciado | — | — |
| L10 Foto de refeição | não iniciado (futuro) | — | — |
| L11 Hardening | não iniciado (opcional) | — | — |

L0 a L4 estão em produção desde a promoção #571 (17/10/2026, 23h10 UTC, deploy 39077773130), que aplicou as migrações 074–083 (25 statements) com o PR #562 (secret do USDA). A próxima promoção leva a 084 do M85 (3 statements) e a 085 do L5a (1 `CREATE TABLE`), 4 statements, com
RUN_DB_MIGRATIONS=true. O L5b não tem migração; leva 1 env nova na `musclefit-workout-ai`
(`OPENAI_NUTRITION_PARSE_MODEL`, 1 recurso alterado in-place no plano do Terraform).

Pré-requisitos fora do código (dono da conta): (a) obter a TACO 4ª ed. em formato legível por máquina e conferir por amostra contra o PDF (L3): ATENDIDO, com a cópia versionada em `docs/nutricao/fontes/` (L3a, PR #563); (b) chave da API do USDA FoodData Central (data.gov): ATENDIDO em 2026-10-17, o usuário criou o secret `musclefit/usda-api-key`, importado no Terraform pelo PR #562 sem exceção de IAM; a api-core recebe `USDA_API_KEY_SECRET_ARN` e o código lê `USDA_API_KEY`, como no DEV (L3); (c) se desejar a TBCA, e-mail aos coordenadores (tbca.contato@usp.br) pedindo autorização de uso não comercial em app pessoal; (d) autorizar a execução única do sync do Intervals com janela ampliada (`days` ≈ 150) para ligar os treinos de jul–ago/2026 ainda sem kcal/FC (L0; escrita em produção pelo job idempotente existente; o relógio Zepp fica fora do plano, decisão de 2026-10-15).

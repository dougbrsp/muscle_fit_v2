# Plano do motor de cardio (M98) — 2026-10-26

Épico M98. Este documento é o plano; nenhuma linha de código muda neste PR. Cada fase abaixo vira um PR
próprio para `develop`, delegado ao especialista, com briefing que cita a regra "Motores: fonte única da
verdade" (`.claude/CLAUDE.md`), o mapa `docs/motores/FONTES_DA_VERDADE.md` e a guarda
`api/tests/unit/test_guarda_fontes_da_verdade.py`.

## 1. Objetivo e requisitos do usuário

Transformar o cardio num motor determinístico, com prescrição estruturada no Modelo de treino,
recomendação por IA validada pelo motor e um dashboard de acompanhamento.

**Prescrição (sem equipamento: o cardio é descrito por tempo e zona de FC, não por máquina):**

| Tipo | Campos |
|---|---|
| Cardio Normal (contínuo) | aquecimento, tempo de cardio, zona de FC desejada (Z1–Z5), tempo de recuperação |
| HIIT | aquecimento, tempo em zona baixa, tempo em zona alta, qual a zona baixa, qual a zona alta, nº de séries, recuperação |

**Recomendação:** considera os treinos anteriores (força e cardio), os treinos futuros do plano e os
dados do Intervals.icu (atividades, zonas e wellness).

**Dashboard:** acompanhamento do cardio planejado e realizado.

## 2. Decisões do usuário (26/10) e premissas

Decisões:

1. **Zonas de FC** vêm do Intervals.icu (configuração do atleta, `GET /athlete/{id}`), com fallback
   calculado a partir de uma FCmáx informada manualmente pelo usuário.
2. **Botão "aplicar no Modelo"** da recomendação cria um **RASCUNHO** do Modelo (fluxo de versionamento
   existente: rascunho → publicar). Nunca altera a versão publicada.
3. **Periodização** (geração de Modelo por IA) só prescreve cardio na fase **C5**.
4. **Modo Treino:** ao encerrar a força, o app apenas **EXIBE** o cardio prescrito, com detalhes. O
   realizado vem do Intervals. A execução guiada no relógio é o backlog **M98-R**.

Premissas (a confirmar com o usuário na revisão deste plano):

- "Recuperação" = desaquecimento (volta à calma ao final), não o intervalo entre tiros do HIIT. No HIIT,
  o "tempo em zona baixa" é o intervalo entre tiros.
- Tempos do HIIT (zona baixa e zona alta) em **segundos**; aquecimento, tempo de cardio contínuo e
  recuperação em **minutos**.

## 3. Estado atual (levantado em `origin/develop`, 2026-10-26)

| Tema | Onde | Observação |
|---|---|---|
| Item cardio do Modelo | `api/db/migrations/007_workout_plan_templates.sql:95` (`item_kind ENUM('muscle_group','exercise','cardio')`), `:106` (`cardio_duration_minutes INT NULL`) | Só duração. Sem zona, sem tipo, sem HIIT |
| Pydantic do item | `api/app/models.py:389` (`cardio_duration_minutes`) | — |
| Validação do item | `api/app/workout_plan_templates.py:449-458` (cardio exige `cardio_duration_minutes > 0`) | Regra fora de motor; passa para `cardio_prescription` |
| Versionamento | `api/app/template_versions.py:115` (`cardio_duration_minutes` na lista de campos versionados) | Novos campos precisam entrar aqui |
| Editor | `frontend/src/app/(app)/modelos-treino/[id]/SlotEditor.tsx:583-585` (botão "Adicionar cardio") | Só minutos |
| Tempo de treino | `api/app/periodization/time_estimate.py:59` (`SECONDS_PER_CARDIO_MINUTE`), `:135-136` (cardio = minutos × 60); espelho `frontend/src/lib/template-time-estimate.ts:18,114` | Não conhece aquecimento/HIIT |
| Rascunho de ficha | `api/app/workout_plan_templates_engine.py:208` (`cardio_minutes` só no dia de descanso), `:624-630,872` (soma); `generation/mode_a.py:939`, `mode_b.py:811` (sempre `None`) | **Lacuna:** nenhum consumidor no frontend (`grep cardio_minutes frontend/src` vazio); só o relatório lê (`report/markdown_report_service.py:733`, `markdown_renderer.py:861`). O cardio do Modelo se perde na ficha |
| Periodização | `api/app/periodization/output_schema.py:27-29` | LLM não prescreve cardio (decisão de PR anterior) |
| Dono atual | `api/app/domain/cardio_policy.py` (PAI 75, escada de Z2 20/30/40/45, Z2 60–70% VO2max, HIIT 5× [2/2 min], memória adaptativa) | Linha 143 do mapa; guarda `numeros_prompt_cardio` (linha 457) |
| Agente de cardio | `api/app/cardio_agent_ai.py:38` (`PROMPT_VERSION = "cardio-agent-v2"`), `:62` (`SYSTEM_PROMPT`), `:115` (saída markdown livre), `:201-205` (`recommendation_markdown`) | Saída não estruturada; nada é validado pelo motor |
| Contexto do agente | `api/app/intelligence/cardio_context_service.py:36-91` (só `activities` de força), `:113-169` (`intervals_wellness`), `:253-262` (`build_context`, 14 dias) | **Lacuna:** não lê `intervals_activities` (cardio realizado), nem treinos futuros do plano. Usa `CURDATE()` (`:91,144`), sem fuso do usuário |
| Cliente Intervals | `api/app/intervals_client.py:117-139` (`/activities`, `/wellness`) | Não busca `/athlete/{id}` (zonas) |
| Atividade crua | `api/db/migrations/027_integracoes_metricas_schema.py:193-209` (`intervals_activities`, `avg_hr_bpm`, `max_hr_bpm`, `raw_json`) | Tempo em zona não tem coluna; pode já estar em `raw_json` (a confirmar na C2) |
| "O que é cardio" (dono) | `api/app/intervals_activity_kind.py:34-106` (`classify`, `is_cardio`) | Dono declarado no próprio módulo |
| Duplicação 1 | `api/app/intervals_integration.py:895-899` (`_is_cardio_activity` com `_CARDIO_PATTERNS` próprio) | Cópia paralela de `intervals_activity_kind` |
| Duplicação 2 | `api/app/analytics.py:148,452` (`LOWER(a.activity_type) LIKE '%cardio%'`) | Terceira definição, em SQL |
| Dashboard ao vivo | `api/app/intervals_integration.py:1020-1029` (`GET /integrations/intervals/cardio-dashboard`, busca no Intervals a cada pedido); tela `frontend/src/app/(app)/cardio/page.tsx:141-142` | Caminho 1 |
| Dashboard por cache | `api/app/intelligence/dashboard/cardio_service.py:69` (usa `intervals_activity_kind`), `:200-246` (lê `intervals_activities`), `:575` (`build_cardio`); tela `frontend/src/app/(app)/dashboard/cardio/DashboardCardioClient.tsx:41` | Caminho 2. Os dois caminhos podem divergir |
| RAG | `rag/markdown/CARDIO.md` (1489 linhas) excluído do índice em `api/app/rag_chunking.py:176-185` (`EXCLUDED_FILES`) | Contém números à mão (ex.: "65% a 75% da FCmáx", "20 a 40 minutos", linhas 123, 145, 179-181) |

## 4. Donos (regra dos motores)

### 4.1 Motores

| Dono | Responsabilidade |
|---|---|
| `api/app/domain/cardio_prescription.py` (**novo**, Python puro) | Tipos (`CONTINUOUS`, `HIIT`), dataclass da prescrição, validação (campos obrigatórios por tipo, zona baixa < zona alta, unidades), padrões (valores iniciais do editor), tetos (máximos por campo e por sessão), tempo total da prescrição em segundos (`total_seconds`), texto canônico da prescrição para exibição ("10 min aquec. + 30 min Z2 + 5 min recuperação"; "8× [30 s Z4 / 90 s Z2]") |
| `api/app/domain/hr_zones.py` (**novo**, Python puro) | Zonas do atleta em bpm; precedência Intervals → FCmáx manual → sem zonas; fallback por % da FCmáx com constantes nomeadas; mapeamento das zonas do Intervals (pode ter 7) para Z1–Z5 do app; classificação de uma FC numa zona |
| `api/app/domain/cardio_policy.py` (**ampliar**) | Política da recomendação: escada de Z2, protocolo de HIIT, PAI. Ganha o **envelope determinístico de interferência** (ex.: HIIT vetado/reduzido em dia de perna ou véspera de perna; teto de HIIT por semana; carga com TSB muito negativo) e a **aderência** planejado × realizado (limiares nomeados) |
| `api/app/intervals_activity_kind.py` (**mantido como único**) | "O que é cardio". `intervals_integration._is_cardio_activity` e o `LIKE '%cardio%'` de `analytics.py` passam a consumir o dono (ou são apagados com o dashboard ao vivo) |
| `api/app/periodization/time_estimate.py` (**consome**) | Tempo do item cardio = `cardio_prescription.total_seconds(item)`; deixa de multiplicar minutos × 60 por conta própria |

### 4.2 Quem consome o quê

| Consumidor | Consome |
|---|---|
| Validação do Modelo (`workout_plan_templates.py`) | `cardio_prescription.validate` |
| Editor do Modelo (`SlotEditor.tsx`) | padrões, tetos e tempo vindos do backend (`motor-constants.generated.json` + endpoint de preview); sem número à mão |
| `time_estimate.py` e espelho TS | `cardio_prescription.total_seconds` |
| Agente de cardio v3 | contexto montado com `hr_zones`, `intervals_activity_kind`, `cardio_policy`; saída validada por `cardio_prescription.validate` e filtrada pelo envelope de `cardio_policy` |
| Dashboard (único) | `hr_zones` (minutos por zona), `cardio_policy` (aderência), `intervals_activity_kind` |
| Modo Treino | texto canônico de `cardio_prescription` (só exibe) |
| Periodização (C5) | `cardio_prescription` (schema e validação), `cardio_policy` (envelope) |
| Prompts e `CARDIO.md` | números interpolados de `cardio_policy`/`cardio_prescription`/`hr_zones` |

### 4.3 Unificações

- **"O que é cardio":** um só dono, `intervals_activity_kind`. Nova guarda `cardio_paralelo` (padrões
  `_CARDIO_PATTERNS`, `_is_cardio_activity`, `LIKE '%cardio%'`) com allowlist inicial com as cópias
  atuais, que encolhe a zero na C4.
- **Leitura do Intervals no dashboard:** um só caminho, o do cache (`intervals_activities`, mantido pelo
  sync). `GET /integrations/intervals/cardio-dashboard` e `/cardio` passam a ler o mesmo serviço
  (`intelligence/dashboard/cardio_service.py`) ou são aposentados com redirecionamento para
  `/dashboard/cardio`.
- **Espelho TS do tempo:** o `template-time-estimate.ts` ganha o cardio estruturado como espelho
  **gerado do backend** (constantes em `motor-constants.generated.json`) com fixture de paridade
  compartilhada (mesmo padrão do E7, linha 160 do mapa).

### 4.4 Linhas novas propostas para o mapa

Na §3.4 (ou §3.5, ao lado do tempo de treino):

```
| Prescrição de cardio do Modelo: tipos contínuo/HIIT, campos obrigatórios por tipo, unidades (s/min), padrões, tetos, tempo total, texto canônico | `domain/cardio_prescription.py` | validação de cardio em `workout_plan_templates.py`; minutos × 60 em `time_estimate`; padrão/teto à mão no editor ou prompt | `cardio_duration_minutes > 0` em `workout_plan_templates.py:455` | M98 C0 | `cardio_prescricao_paralela` |
| Zonas de FC do atleta: precedência Intervals → FCmáx manual; % da FCmáx do fallback; mapeamento 7 → 5 zonas; FC → zona | `domain/hr_zones.py` | % de FCmáx ou limite de zona escrito fora do dono (py, ts, prompt, RAG) | `CARDIO.md` linha 123 ("65% a 75% da FCmáx") | M98 C0/C2 | `zonas_fc_paralelas` |
| Envelope de interferência força × cardio e aderência planejado × realizado | `domain/cardio_policy.py` | veto de HIIT, teto semanal ou limiar de aderência fora do dono | — | M98 C2/C3 | `numeros_prompt_cardio` (ampliada) |
| O que é cardio (atividade do Intervals) | `intervals_activity_kind.py` | `_is_cardio_activity`/`_CARDIO_PATTERNS` próprios, `LIKE '%cardio%'` | `intervals_integration.py:895`, `analytics.py:148,452` | M98 C2/C4 | `cardio_paralelo` |
```

E as guardas novas na tabela de padrões (perto da linha 433/457):

```
| `cardio_prescricao_paralela` | `cardio_duration_minutes` comparado a número ou multiplicado por 60 fora do dono; constantes `CARDIO_*`/`HIIT_*` com número fora do dono (py e ts) | `domain/cardio_prescription.py` | M98 | C0 (nasce com a allowlist das cópias atuais, que zera na C0) |
| `zonas_fc_paralelas` | "N% da FCmáx", "N-M% FC", `max_hr * 0.x` (py, ts e RAG) | `domain/hr_zones.py` | M98 | C0 (allowlist: `CARDIO.md`, zera na C3) |
| `cardio_paralelo` | `_is_cardio_activity`, `_CARDIO_PATTERNS`, `LIKE '%cardio%'` | `intervals_activity_kind.py` | M98 | C2 (allowlist: 2 arquivos, zera na C4) |
```

## 5. Fases

Regras comuns a todas as fases:

- Um PR por fase para `develop`, branch nascida de `origin/develop`.
- O especialista roda `guardiao-motores` e `revisor-impacto-producao` antes do primeiro push; correções
  de uma rodada vão num push só.
- Só testes unitários localmente (seção "Testes e economia de tokens" do agente); integração no CI.
- Rótulo `ci-completo` obrigatório quando houver SQL/migração/`db_dataapi`/`db_pymysql`, **só no último
  push**, antes do merge.
- Migração: número confirmado na hora (a última em `develop` hoje é a `094_deload.py`, então
  provavelmente 095+); colunas anuláveis; idempotente (checa coluna antes de criar); sem FK para
  `exercise_catalog`; reversível documentada.
- Cada PR atualiza o mapa e mantém a guarda verde; mudança de semântica traz "Impacto em produção"
  medido (SELECT de leitura em produção).

### C0 — Fundação (backend, sem tela nova)

- **Escopo:** `domain/cardio_prescription.py` e `domain/hr_zones.py` (com FCmáx manual); migração do item
  do Modelo; `time_estimate` consumindo o motor; campos novos no versionamento do Modelo; validação do
  Modelo pelo motor; correção do `cardio_minutes` do rascunho (o cardio do slot chega à ficha em dia de
  treino e de descanso, no formato estruturado, e o relatório lê do motor).
- **Arquivos prováveis:** `api/app/domain/cardio_prescription.py`, `api/app/domain/hr_zones.py`,
  `api/app/models.py`, `api/app/workout_plan_templates.py`, `api/app/template_versions.py`,
  `api/app/periodization/time_estimate.py`, `api/app/workout_plan_templates_engine.py`,
  `api/app/generation/mode_a.py`/`mode_b.py`, `api/app/intelligence/report/*`, gerador do
  `motor-constants.generated.json`, `frontend/src/lib/template-time-estimate.ts` + fixture de paridade,
  `docs/motores/FONTES_DA_VERDADE.md`, guarda.
- **Migração:** em `workout_plan_template_slot_items` (e na tabela de itens versionados, se separada):
  `cardio_mode` (`continuous`/`hiit`), `cardio_warmup_minutes`, `cardio_cooldown_minutes`,
  `cardio_target_zone`, `hiit_low_seconds`, `hiit_high_seconds`, `hiit_low_zone`, `hiit_high_zone`,
  `hiit_rounds`. Todas anuláveis. **Backfill:** item com `cardio_duration_minutes` vira
  `cardio_mode='continuous'` (zona nula = "não definida"; aquecimento/recuperação nulos = 0). Em
  tabela de perfil do usuário (nome a confirmar): `max_hr_bpm_manual` anulável. `cardio_duration_minutes`
  continua sendo o tempo do contínuo.
- **Testes unitários:** validação por tipo, tetos, padrões, `total_seconds` (contínuo e HIIT), precedência
  das zonas, fallback por FCmáx, paridade Python × TS do tempo, backfill (idempotência).
- **Impacto em produção a medir:** `SELECT COUNT(*) FROM workout_plan_template_items WHERE item_kind='cardio'`
  e quantos Modelos publicados/rascunhos têm cardio; tempo estimado do slot antes × depois (deve ser
  idêntico para o contínuo sem aquecimento/recuperação).
- **`ci-completo`:** sim (migração), no último push.

### C1 — Editor do Modelo (Normal/HIIT)

- **Escopo:** no `SlotEditor`, o item cardio ganha seletor Normal/HIIT e os campos da seção 1; padrões,
  tetos e tempo do slot vêm do backend; validação exibida a partir do 422 do backend; o tempo do slot
  usa o espelho gerado.
- **Arquivos prováveis:** `frontend/src/app/(app)/modelos-treino/[id]/SlotEditor.tsx` e componentes
  vizinhos, tipos em `frontend/src/lib`, endpoint de preview do Modelo se precisar devolver o texto
  canônico.
- **Migração:** não.
- **Testes:** unitários do frontend para o formulário e paridade; unitários do backend do texto canônico.
- **Impacto em produção:** só tela; salvar um Modelo com cardio contínuo existente não muda nada.
- **`ci-completo`:** não.

### C2 — Realizado no Intervals

- **Escopo:** buscar as zonas do atleta (`GET /athlete/{id}`, configuração de FC por esporte) e guardar;
  `hr_zones` passa a usar a precedência Intervals → manual; tempo em zona por atividade (campo do
  payload da atividade, provavelmente `icu_hr_zone_times`, **a confirmar**; tentar extrair primeiro do
  `raw_json` já gravado antes de buscar de novo); planejado × realizado (aderência) no `cardio_policy`;
  consolidar "o que é cardio" no backend (`intervals_integration._is_cardio_activity` consome o dono).
- **Arquivos prováveis:** `api/app/intervals_client.py` (`fetch_athlete`), `api/app/intervals_sync.py`,
  `api/app/intervals_store.py`, `api/app/domain/hr_zones.py`, `api/app/domain/cardio_policy.py`,
  `api/app/intervals_integration.py`.
- **Migração:** tabela de zonas do atleta (`intervals_athlete_hr_zones` ou colunas JSON no perfil, com
  `source` e `fetched_at`) e coluna `hr_zone_seconds JSON NULL` em `intervals_activities`; backfill a partir
  do `raw_json` quando o campo existir.
- **Testes:** parse das zonas (5 e 7 zonas), mapeamento para Z1–Z5, tempo em zona, aderência, precedência.
- **Impacto em produção:** quantas atividades de cardio têm o campo no `raw_json`; usuários com Intervals
  configurado; chamadas extras ao Intervals por sync (uma por sync, no máximo).
- **`ci-completo`:** sim (migração), no último push.

### C3 — Recomendação por IA estruturada

- **Escopo:** agente `cardio-agent-v3` com saída JSON (schema = prescrição de `cardio_prescription`,
  lista de sessões por dia) validada pelo motor; contexto com força e cardio anteriores
  (`activities` + `intervals_activities` via `intervals_activity_kind`), treinos futuros do plano (slots
  do Modelo ativo), wellness (CTL/ATL/TSB, HRV, FC de repouso) e zonas do atleta; janela no fuso do
  usuário (`time_windows`); envelope determinístico de interferência no `cardio_policy` aplicado DEPOIS da
  IA (a IA propõe, o motor corta/ajusta e registra o motivo); `CARDIO.md` reescrito sem números à mão
  (interpolados ou remetidos ao sistema) e incluído no RAG (sai de `EXCLUDED_FILES`); botão "aplicar no
  Modelo" que cria rascunho; exibição do cardio prescrito no Modo Treino ao encerrar a força; evals e
  snapshot do prompt (`docs/ia/EVALS.md`).
- **Arquivos prováveis:** `api/app/cardio_agent_ai.py`, `api/app/cardio_generation_core.py`,
  `api/app/cardio_recommendations.py`, `api/app/intelligence/cardio_context_service.py`,
  `api/app/domain/cardio_policy.py`, `api/app/rag_chunking.py`, `rag/markdown/CARDIO.md`,
  `frontend/src/app/(app)/agente-cardio/*`, tela do Modo Treino, endpoint de rascunho do Modelo.
- **Migração:** coluna `recommendation_json JSON NULL` (e `prompt_version`) em `cardio_recommendations`,
  se ainda não existir; o markdown continua para o histórico.
- **Testes:** validação da saída (JSON inválido, zona inexistente, teto estourado), envelope (HIIT em dia
  de perna), aplicação no rascunho (nunca na versão publicada), snapshot do prompt, guarda de números.
- **Impacto em produção:** custo de tokens por recomendação (antes × depois); recomendações existentes
  continuam legíveis; nenhum Modelo publicado muda.
- **`ci-completo`:** sim se houver a coluna nova; senão não.
- **Divisão (2026-10-26):** a C3 virou dois PRs. **C3a** (backend: agente v3, contexto, envelope `domain/cardio_envelope.py`, RAG, migração 098, zonas no sync manual; mapa §69.4) e **C3b** (botão "aplicar no Modelo" num rascunho e exibição no Modo Treino), que consome o contrato `plan_link` de `api/app/cardio_recommendation_v3.py`.

### C4 — Dashboard de acompanhamento

- **Escopo:** minutos por zona por semana, contínuo × HIIT, planejado × realizado (aderência),
  CTL/ATL/TSB, HRV e FC de repouso, próxima sessão recomendada; unificar as duas leituras do Intervals
  (aposentar o caminho ao vivo ou fazê-lo ler o serviço do cache); remover o `LIKE '%cardio%'` de
  `analytics.py`; guarda `cardio_paralelo` zerada.
- **Arquivos prováveis:** `api/app/intelligence/dashboard/cardio_service.py`,
  `api/app/intervals_integration.py`, `api/app/analytics.py`,
  `frontend/src/app/(app)/dashboard/cardio/*`, `frontend/src/app/(app)/cardio/page.tsx`.
- **Migração:** não prevista (a confirmar se `analytics.py` precisa de coluna nova).
- **Testes:** agregações por zona/semana, aderência, paridade da classificação.
- **Impacto em produção:** números da tela nova × antiga para o usuário 1 (mesma janela); quem usa
  `/cardio` hoje.
- **`ci-completo`:** sim se mexer em SQL de `analytics.py` (regra do rótulo para SQL).
- **Entregue (2026-10-27):** mapa §69.6. `/cardio` e o endpoint ao vivo removidos (`/cardio` → 301 para
  `/dashboard/cardio`); casamento do cardio em dia de descanso proposto em `intervals_hr_match.match_local_day`,
  sem consumidor até a decisão do usuário; contexto v2 sem `CURDATE()`.

### C5 — Periodização com cardio

- **Escopo:** `output_schema` aceita item cardio estruturado; validação semântica pelo
  `cardio_prescription` e envelope do `cardio_policy`; prompt de periodização interpola as regras; evals.
- **Arquivos prováveis:** `api/app/periodization/output_schema.py`, `validation.py`, `context.py`,
  prompt de periodização, `docs/ia/EVALS.md`.
- **Migração:** não.
- **Testes:** schema, validação, snapshot do prompt (`periodization-v*` sobe).
- **Impacto em produção:** só Modelos gerados depois do deploy; custo de tokens.
- **`ci-completo`:** não.

**Ordem:** C0 → C1 → C2 → C3 → C4 → C5. C1 e C2 podem correr em paralelo depois da C0 (arquivos
disjuntos), em worktrees separados. A promoção para `main` é em lote e com aval do usuário.

## 6. Fora do escopo e backlog

- **M98-R:** execução guiada do cardio no relógio (Zepp OS), com alertas de zona e troca de fase do HIIT.
- Prescrição por equipamento (esteira, bike, velocidade, inclinação, potência).
- Prescrição por pace/potência; zonas por esporte diferentes da FC.
- Escrita de treinos planejados no calendário do Intervals.

## 7. Riscos e perguntas em aberto

| Item | Fase | Como validar |
|---|---|---|
| Formato das zonas na API do Intervals (`sportSettings[].hr_zones`? limites superiores em bpm? por esporte?) | C0/C2 | chamada real com a conta do usuário em DEV; registrar o payload anonimizado como fixture |
| Intervals com 7 zonas (Z1–Z7) × app com Z1–Z5 | C2 | decidir mapeamento (ex.: Z5–Z7 → Z5) no `hr_zones`, com aval do usuário |
| Campo do tempo em zona na atividade (`icu_hr_zone_times`?) e se já está no `raw_json` gravado | C2 | SELECT de leitura em produção no `raw_json` |
| Fórmula do fallback por FCmáx (percentuais de cada zona) | C0 | aval do usuário; constantes nomeadas no `hr_zones` |
| Regras de interferência (HIIT × dia de perna, teto de HIIT/semana, TSB) | C3 | calibrar com o histórico do usuário 1; começar conservador e só informativo se houver dúvida |
| Premissa "recuperação = desaquecimento" e unidades (s × min) | C0 | confirmar na revisão deste plano |
| `cardio_minutes` do rascunho: perda confirmada no frontend; confirmar se o rascunho salvo persiste o campo | C0 | teste do caminho Modelo → ficha |
| Dois dashboards hoje: aposentar `/cardio` pode quebrar hábito do usuário | C4 | perguntar antes de remover a rota |
| `CARDIO.md` (1489 linhas) com números à mão: reescrita grande | C3 | fazer em commit separado dentro da C3 |
| Contexto do agente usa `CURDATE()` sem fuso | C3 | migrar para `time_windows` junto do v3 |

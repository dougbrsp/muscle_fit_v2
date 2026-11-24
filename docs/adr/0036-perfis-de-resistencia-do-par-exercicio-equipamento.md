# ADR 0036: Perfis de resistência do par exercício+equipamento, com vocabulário controlado e revisão humana (issue #288)

- **Status**: Aceita. Fatia 1 (lotes 41, 42/42b e 43) nas branches `feat/perfis-resistencia-l41`, `feat/perfis-resistencia-ia-l42b` e `feat/perfis-resistencia-tela-l43`, sem commit. Não promovida a produção.
- **Data**: 2026-11-23
- **Relaciona-se a**: [ADR 0014](0014-contexto-de-equipamento-e-calibracao.md) (equipamento por academia); [ADR 0015](0015-progressao-fonte-unica-no-motor.md) (um único dono por cálculo); [ADR 0035](0035-receitas-como-alimento-composto.md) (molde da estrutura deste registro e da migração com `down` guardado).

## Contexto

- A issue #288 pede o perfil de resistência de um exercício feito num equipamento: como a resistência que o aparelho oferece varia ao longo do movimento (mecanismo, trajetória, perfil ao longo da amplitude). Hoje o sistema não guarda nada disso.
- `equipment_exercises` tem só `equipment_id`, `exercise_id`, `is_preferred`, `is_active` e `notes` (`docs/MUSCLE_FIT.md`, seção 5.3). A tabela é lida pelo catálogo, pela view `vw_muscle_group_equipment_exercises` e pelo cache `gym_equipment` (`api/app/gym_equipment_sync.py`); colunas de revisão nela afetariam todas essas leituras.
- Decisões do usuário em 23/11/2026:
  - a base de conhecimento é `PERFIS_RESISTENCIA_RAG.md` no RAG privado (`api/privado`), com origem, SHA e atribuição registrados;
  - a IA sugere e o admin revisa; a IA nunca publica um perfil;
  - a entrega é em três fatias.
- Medido em 23/11/2026 por leitura (SELECT, sem escrita): 145 pares ativos em `equipment_exercises` (149 no total) e nenhuma tabela de perfil. Logo, nada existia para migrar.
- Restrição do motor: a regra de resistência não pode virar número de torque nem braço de momento. O perfil é classe, e o motor de treino ainda não o consome.

## Alternativas consideradas

- **Perfil no exercício.** Recusado: o mesmo exercício muda de perfil conforme o aparelho (uma máquina guiada e um halter não têm a mesma curva).
- **Perfil no tipo de equipamento.** Recusado: o tipo (máquina de cabo, polia, anilhas) não descreve a trajetória de cada exercício feito nele.
- **Colunas em `equipment_exercises`.** Recusado: a trilha de revisão e o histórico exigiriam colunas de auditoria numa tabela lida pelo catálogo e pelo sincronismo do cache.
- **Vocabulário aberto (texto livre).** Recusado: sem vocabulário fechado não há assinatura comparável nem redundância verificável. Campo ausente vira `unknown`, nunca "constante".
- **IA grava o perfil como verdade.** Recusado pelo usuário: a IA só cria sugestão (`suggested`); só a revisão humana pesa em decisão automática.
- **Números de torque ou vantagem mecânica por aparelho.** Recusado: sem fonte medida por aparelho, o número seria inventado. O perfil é classe.

## Decisão

1. **Dono único: `api/app/domain/resistance_profile.py`** (Python puro, sem I/O). Guarda o vocabulário (`RESISTANCE_VOCABULARY_VERSION = "resistance-v1"`), a validação (`validate_profile`), a regra de transição (`can_transition`), a confiabilidade (`is_reliable`), a assinatura (`signature`, `signature_label`) e o export (`export_vocabulary`). Consumidor: `api/app/resistance_profiles.py`, que só valida pelo dono, lê, grava e serializa.
2. **Perfil no par.** Uma linha por par `equipment_exercise_id` (UNIQUE). Componentes: `source`, `mechanism`, `trajectory`, `profile`, `line`, `direction_changes` e sinalizadores de sensibilidade (`flags`). Ausente vira `unknown`.
3. **Assinatura: `fonte:mecanismo:trajetória:perfil:linha`** (`_SIGNATURE_FIELDS`). Ela só existe quando todos os códigos estão no vocabulário atual; o rótulo legível sai de `signature_label`.
4. **Estados e transições.**
   - `suggested`, `reviewed`, `verified`, `rejected`. Só `reviewed` e `verified` são confiáveis (`RELIABLE_STATUSES`).
   - IA: só cria ou mantém `suggested` (de nada ou de `suggested`). Não sobrescreve estado revisado, verificado ou rejeitado.
   - Admin (revisão humana): grava qualquer estado, inclusive `verified` direto, porque a gravação do admin já é revisão humana. Carimba `reviewed_by` e `reviewed_at` em todo estado que não seja `suggested`.
   - `verified` e `rejected` só por humano. `rejected` só sai por humano.
5. **Sem número de torque, braço de momento ou vantagem mecânica.** O dono só aceita códigos do vocabulário.

## O que fica para depois

- Padrão do equipamento canônico e variação de exemplar ou fabricante: níveis 1 e 3 da issue #288.
- Sugestão por IA a partir da base do RAG: lote 42.
- Tela de revisão do admin: lote 43.
- Motor de redundância (uso do perfil na decisão): fatia 2.
- Geração automática de perfis em lote: fatia 3.

## Modelo de dados (migração 126, `api/db/migrations/126_resistance_profiles.py`)

- `equipment_exercise_resistance_profiles`: uma linha por par. `equipment_exercise_id` → `equipment_exercises(id)` com `ON DELETE CASCADE`; `created_by` e `reviewed_by` → `users(id)` com `ON DELETE SET NULL`. Sem FK para `exercise_catalog` (o par já a tem). Defaults iguais aos do dono: `unknown`, `low`, `suggested`, `ai`.
- `equipment_exercise_resistance_profile_history`: trilha de cada gravação, com `before_json`, `after_json`, `actor_user_id`, `origin` e `reason`. `profile_id` → perfil com `ON DELETE CASCADE`.
- `plan(db)` só lê `information_schema`. Tabela ausente gera um `CREATE TABLE`. Forma diferente da esperada (coluna, tipo, nulabilidade, índice, engine) ou pai incompatível (`users`, `equipment_exercises` com ENGINE ou `id` errado) interrompe com `ResistanceProfilesError` antes do primeiro statement. Em DRY-RUN, pai ausente é aceito.
- A checagem de existência não confere FKs nem a regra de exclusão. É a mesma lacuna do molde 125.

## Contrato das rotas (`api/app/resistance_profiles.py`, montado em `api/app/main.py`)

- Leitura (`exercicios.catalogo`/`ver`): `GET /resistance-profiles/vocabulary`, `GET /resistance-profiles/coverage`, `GET /resistance-profiles` (filtros `status`, `confidence`, `equipment_id`, `exercise_id`, `pending`; paginação de até 200), `GET /resistance-profiles/{equipment_exercise_id}` e `GET /resistance-profiles/{equipment_exercise_id}/history`.
- Escrita (`admin.catalogos`/`editar`): `PUT /resistance-profiles/{equipment_exercise_id}`, substituição completa com `status` obrigatório. A rota força `origin = admin`; a IA não grava por aqui.
- Par sem perfil aparece como `has_profile = false` com componentes `unknown`. Nunca como erro.
- Erros: 422 tipado com `code` e `field` (vocabulário, flag, transição); 404 `pair_not_found`; 409 `profile_conflict` quando outra requisição gravou primeiro; 503 com `resistance_profile_tables_missing`, citando a migração 126, enquanto as tabelas não existem.

## Ordem de deploy e rollback

- **Deploy:** a migração 126 vai antes do código. Sem ela, só as rotas novas respondem 503; o resto da API segue.
- **Rollback do código:** seguro, porque nenhum motor consome o perfil. As tabelas ficam.
- **`down` da 126:** destrutivo e guardado pelo código. `plan_down` recusa se `equipment_exercise_resistance_profiles` tiver qualquer linha. Com a tabela ausente ou vazia, solta o histórico e depois o perfil.
- Havendo perfil salvo, reverter a migração exige desativação por migração nova, como foi feito no caso das receitas (ADR 0035). Nunca por escrita manual em produção.

## Trade-offs

- **Ganha:** um dono para vocabulário, assinatura e transições; histórico completo de cada gravação; nenhuma leitura atual muda.
- **Ganha:** a classe é verificável e não depende de número inventado.
- **Paga:** a cobertura começa em zero. A leitura mostra `unknown` e não estima.
- **Paga:** mudar o vocabulário exige nova versão (`vocabulary_version`) e uma decisão de código, não de dados.
- **Paga:** sem número de torque, o perfil não serve para calcular carga. Isso é intencional.

## Consequências

- **Dados.** Duas tabelas novas (migração 126). Nenhuma alteração em tabela existente.
- **Contrato.** Seis rotas novas em `/resistance-profiles`. Nenhuma rota existente muda de resposta.
- **Mapa.** Dono registrado em `docs/motores/FONTES_DA_VERDADE.md` (seção 3.8). A guarda `api/tests/unit/test_guarda_fontes_da_verdade.py` não foi alterada nesta entrega.
- **Próximas decisões.** O lote 42 decide o fluxo da sugestão por IA (sempre `suggested`); o lote 43, a tela de revisão.

## Impacto em produção

Medido em 23/11/2026 por leitura (SELECT), sem escrita: 145 pares ativos em `equipment_exercises` (149 no total) e nenhuma tabela de perfil. Nada muda para o usuário final: nenhum motor de treino consome o perfil nesta fatia, e a API só ganha rotas que respondem 503 até a migração 126 ser aplicada.

## Rodada de correção (lote 41, #288)

- Regras de revisão, pendente e desconhecido ficam no dono: `stamps_review`, `is_pending`, `is_unknown`, `HUMAN_REVIEW_STATUSES`, `PENDING_STATUSES`, `ORIGIN_AI`/`ORIGIN_ADMIN`. A rota só consome esses símbolos.
- Gravação do admin zera `model_version` (`api/app/resistance_profiles.py`, UPDATE de revisão): o perfil deixa de ser atribuído à IA.
- Deadlock na criação concorrente (MySQL 1213, `_is_deadlock_error`) responde 409 `profile_conflict`, igual à chave duplicada.
- Contagem corrigida: são seis rotas, não sete.
- Sem padrão de guarda para o vocabulário ainda; entra com os lotes 42 e 43.

## Lote 42 (2026-11-23, #288, sugestão de perfil por IA)

Branch `feat/perfis-resistencia-ia-l42`, sem commit. Não promovido a produção.

- **Fluxo.** O admin do catálogo chama `POST /resistance-profiles/suggest` (`admin.catalogos`/`editar`, `api/app/resistance_profiles.py`). Com `equipment_exercise_ids`, usa esses pares; sem ids, pega os pares pendentes até `limit`. A rota cria um job (`SUGGEST_JOB_TYPE` = `resistance-profile-suggest`, no dono) e o despacha para a Lambda `musclefit-workout-ai`, pelo roteador de `workout_ai_generation_core.py` (import tardio de `resistance_profile_ai`). Responde 202 com `{job_id}`; o status sai de `GET /jobs/{job_id}`. Sem pares, responde `{job_id: null, pairs: 0}`.
- **Worker** (`api/app/resistance_profile_ai.py`). Lê os pares numa consulta; por par, uma chamada do modelo leve; valida pelo dono (`validate_response` força origem `ai` e status `suggested`); grava só se `can_transition` permitir. A IA só cria `suggested` e nunca sobrescreve `reviewed`, `verified` ou `rejected`: esses pares viram `skipped`, sem chamada à IA. Falha por par (IA, resposta inválida, gravação, par inexistente) entra em `failed` com código (`ai_failed`, `invalid_response`, `write_failed`, `pair_not_found`) e não derruba o lote. Cada gravação gera linha na trilha, com `origin = ai`.
- **Limites.** Teto de `SUGGEST_MAX_PAIRS` pares por job, constante do dono (6 desde o lote 42b; era 10). O orçamento de 40 s (`TIME_BUDGET_SECONDS`) saiu: vale o timeout da Lambda, ver lote 42b. Pares sem tempo saem como `not_processed` e o admin repete.
- **Onde ficam prompt e RAG.** Prompt `resistance_profile/suggest` em `api/privado/prompts/resistance_profile/`, no submódulo privado `api/privado` (repositório `muscle_fit_privado`). RAG `PERFIS_RESISTENCIA.md`, lido por `rag_chunking` com teto `RAG_MAX_CHARS`. Origem e SHA ficam no cabeçalho do arquivo dentro do submódulo; nada do texto vai para este repositório público. O vocabulário entra no prompt interpolado de `export_vocabulary()`. A decisão de 23/11 citava `PERFIS_RESISTENCIA_RAG.md`; o nome em uso no código é `PERFIS_RESISTENCIA.md`.
- **Custo.** Uma chamada do modelo leve por par. Default `gpt-5.4-mini` (env `OPENAI_RESISTANCE_PROFILE_MODEL`); feature `exercise_summary`, que o roteamento do provedor leva ao Haiku. Com 145 pares ativos em produção (23/11), a base inteira são até 145 chamadas, em jobs de até 6 pares (`SUGGEST_MAX_PAIRS`; o trecho dos 10 pares estava obsoleto, ver lote 42b).
- **Sem cota por usuário.** A rota é só do admin do catálogo, então não há cota de IA por usuário (sem `ai_usage_log`). O teto é por job.
- **Guarda.** Padrão `resistencia_codigos_fora_do_dono` (allowlist vazia) em `api/tests/unit/test_guarda_fontes_da_verdade.py`.
- **Impacto em produção** (medido em 23/11/2026 por leitura, sem escrita): nenhuma tela de usuário final muda. A rota só responde ao admin, e o job só grava `suggested`.
- **Pendências e não verificado (atualizado no lote 42b).** (a) Resolvido no lote 43, por leitura: a rota não corta as ids. `ResistanceSuggestIn` (`api/app/request_models.py`) recusa mais de `SUGGEST_MAX_PAIRS` (6) com 422 antes de criar o job; o worker marca como `not_processed` só os pares sem tempo. (b) Resolvido por leitura: cada chamada usa `min(50 s, restante)`, então nunca passa do orçamento da Lambda (lote 42b). (c) SHA do texto privado não conferido. (d) Nenhum teste executado neste lote.

## Lote 42b (2026-11-23, #288, tempo, worker perdido e RAG)

Branch `feat/perfis-resistencia-ia-l42b`, sem commit. Não promovido a produção. Corrige o lote 42 acima (teto 10 e orçamento de 40 s estavam obsoletos). Conferido no código do checkout.

- **Tempo.** `musclefit-workout-ai` tem timeout de 60 s (`infra/lambda_workout_ai.tf`; `infra/` não mudou nesta branch). `suggest_pairs` marca o início antes de ler os pares e o RAG, e reserva `RESERVE_SECONDS` = 8 s (cold start e escrita do resultado). Cada chamada usa `min(CALL_TIMEOUT_SECONDS, restante)`, com `CALL_TIMEOUT_SECONDS` = `LLM_TIMEOUT_LIVE_SECONDS` = 50 s (`openai_client.py`). Com menos de `MIN_CALL_SECONDS` = 5 s de restante, o par sai em `failed` como `not_processed`.
- **Teto.** `SUGGEST_MAX_PAIRS` = 6 (`api/app/domain/resistance_profile.py`), para ~52 s úteis e ~8 s por chamada. **Não medido:** os ~8 s são estimativa, não medição de p95 do modelo.
- **Worker perdido.** `async_jobs.JOB_TYPE_LAMBDA` mapeia `resistance-profile-suggest` → `musclefit-workout-ai`. Job sem resultado vira `failed`/`worker_lost` após 60 + 60 s (M73). Sem mudança no `get_job`.
- **RAG.** `_rag_text` chama `rag_storage.ensure_rag_local()` antes de ler `PERFIS_RESISTENCIA.md`. Em produção o arquivo vem do S3 para `/tmp/rag`. Sem o arquivo, a sugestão segue com `(trecho indisponível)`. O objeto é publicado por `aws_s3_object.musclefit_rag_markdown` (`infra/s3_rag.tf`, fileset sobre `api/privado/rag/markdown`).
- **Impacto em produção** (23/11/2026): nenhuma tela de usuário final muda; a rota é só do admin. Para a promoção:
  1. O commit do submódulo `api/privado` precisa estar publicado no repositório privado `muscle_fit_privado` antes do push do repositório público. Sem isso, o CI não tem o prompt nem o RAG.
  2. A promoção precisa aplicar o terraform do RAG com `run_terraform=true`, para publicar `PERFIS_RESISTENCIA.md` no S3. Plan esperado: 1 objeto novo. **Não verificado:** o plan não foi executado.
  3. Smoke pós-promoção: `POST /resistance-profiles/suggest` com 1 par ativo; depois `GET /jobs/{job_id}` até `done` ou `failed`. Conferir que o par ficou `suggested` e que a trilha tem `origin = ai`.
- **Não verificado nesta rodada:** build do `api/Dockerfile.ai` (não executado); plan do terraform (não executado); testes unitários e guarda (não executados); SHA do texto privado (não conferido).

## Lote 43 (2026-11-23, #288, fatia 1 parte C: tela)

Branch `feat/perfis-resistencia-tela-l43`, sem commit. Não promovido a produção. Sem mudança de API nem de banco: a tela só consome as rotas dos lotes 41 e 42b.

- **Cobertura.** `ResistanceProfilesTab` (`frontend/src/app/(app)/equipamentos-exercicios/ResistanceProfilesTab.tsx`) lê `GET /resistance-profiles/coverage`: pares ativos, com perfil, sem perfil, desconhecidos e contagem por estado, com rótulos do vocabulário.
- **Lista.** `GET /resistance-profiles` com filtros de estado, confiança, equipamento, exercício e "só pendentes" (`pending`); 25 por página (`PAGE_SIZE`). Par sem perfil aparece como "Sem perfil", nunca como erro. Cliente: `frontend/src/lib/resistance-profiles-api.ts` (`fetchProfiles`, `buildListQuery`).
- **Editor.** Um seletor por componente, com as opções de `GET /resistance-profiles/vocabulary` (`source`, `mechanism`, `trajectory`, `profile`, `line`, `direction_changes`); `flags` em múltipla escolha; confiança, estado, justificativa, evidência e motivo. Salva com `PUT /resistance-profiles/{id}` (substituição completa). O admin escolhe o estado entre todos os do vocabulário, inclusive `verified`.
- **Aprovar e rejeitar.** Os botões rápidos gravam o estado que a API informa em `actions` (`APPROVE_STATUS` = `reviewed`; `REJECT_STATUS` = `rejected`, no dono). O front não escreve código de estado. Aparecem só para par com perfil. `verified` não tem botão rápido: sai pelo editor.
- **Estado inicial e confirmação.** O editor começa no estado que o dono informa em `export_vocabulary()["default_status"]` (`ADMIN_DEFAULT_STATUS` = `APPROVE_STATUS`, hoje `reviewed`) quando o par não tem perfil; par com perfil abre com o estado gravado. O front não deduz o estado pela ordem da lista nem por outro campo. Aprovar e rejeitar abrem um diálogo de confirmação com motivo opcional ("Motivo (opcional)") e só gravam ao confirmar. Razão da confirmação: o botão da lista grava de imediato, sem o par à vista; o diálogo dá a chance de revisar antes.
- **Histórico.** `GET /resistance-profiles/{id}/history`; a tela mostra data, origem, motivo e campos alterados (`changedFields`).
- **Sugerir por IA.** Sem seleção, pede os pendentes; com seleção, pede os pares marcados. O teto vem de `suggest_max_pairs` (`SUGGEST_MAX_PAIRS`, 6): acima dele o botão fica desligado e a mensagem mostra o limite. O resultado vem do job (`pollAsyncJob`) e mostra sugeridos, pulados e falhas com código. Quem decide o teto é o dono e `ResistanceSuggestIn` (422 acima de 6); a tela só o exibe.
- **Permissões.** A aba só aparece com leitura (`exercicios.catalogo`/`ver`). Checkboxes, sugerir, aprovar, rejeitar e editar exigem `canEdit` (`admin.catalogos`/`editar`). Servidor é a autoridade: 403 vira mensagem; 422 tipado vira campo e código.
- **Sem regra no front.** A tela não tem código de vocabulário, transição, assinatura nem regra de revisão. `FIELD_TITLES` são rótulos de apresentação dos componentes.
- **Não verificado neste lote:** a origem de `canView`/`canEdit` no componente pai `EquipamentosExerciciosClient.tsx` (não relido aqui); os testes e a guarda (não executados); a tela no navegador.

**Fechamento da fatia 1.** Lote 41: dono, tabelas (migração 126) e seis rotas. Lotes 42 e 42b: sugestão por IA. Lote 43: tela. Pendências antes da promoção: aplicar a migração 126 antes do código (sem ela, as rotas respondem 503); publicar o commit do `api/privado` (prompt e RAG) antes do push do repositório público, como no lote 42b; rodar o smoke de sugestão e a guarda.

**Próximas fatias.**
- **Fatia 2: motor de redundância.** Uso do perfil na decisão, com aviso no editor manual de Modelo quando o exercício escolhido for redundante com outro. O motor dono fica registrado no mapa antes de qualquer consumidor. A regra de redundância é decisão a tomar com o usuário.
- **Fatia 3: geração automática e diagnóstico.** Geração de perfis em lote e diagnóstico de cobertura e qualidade.
- **Fora das fatias 1 a 3:** padrão do equipamento canônico e variação de exemplar (níveis 1 e 3 da #288).

**Impacto em produção** (por leitura do código, não medido em produção): só usuários com acesso ao catálogo veem a aba, e só depois da promoção. A aba só chama rotas que respondem 503 enquanto a migração 126 não estiver aplicada.

## Verificação

Este ADR não registra execução de testes. Arquivos de teste da entrega (não executados nesta tarefa):

- `api/tests/unit/test_resistance_profile.py` (dono);
- `api/tests/unit/test_resistance_profiles_routes.py` (rotas);
- `api/tests/unit/test_migration_126_plano.py` (plano da migração);
- `api/tests/integration/dataapi/test_resistance_profiles_dataapi.py` (só roda no CI).

Não verificado neste ADR: a execução desses testes, a guarda de fontes, a aplicação da migração em banco real e qualquer tela.

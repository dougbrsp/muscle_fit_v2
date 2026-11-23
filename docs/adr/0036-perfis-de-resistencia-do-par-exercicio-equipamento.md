# ADR 0036: Perfis de resistência do par exercício+equipamento, com vocabulário controlado e revisão humana (issue #288)

- **Status**: Aceita. Fatia 1, parte A (lote 41) na branch `feat/perfis-resistencia-l41`, sem commit. Não promovida a produção.
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

## Verificação

Este ADR não registra execução de testes. Arquivos de teste da entrega (não executados nesta tarefa):

- `api/tests/unit/test_resistance_profile.py` (dono);
- `api/tests/unit/test_resistance_profiles_routes.py` (rotas);
- `api/tests/unit/test_migration_126_plano.py` (plano da migração);
- `api/tests/integration/dataapi/test_resistance_profiles_dataapi.py` (só roda no CI).

Não verificado neste ADR: a execução desses testes, a guarda de fontes, a aplicação da migração em banco real e qualquer tela.

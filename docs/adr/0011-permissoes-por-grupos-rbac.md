# ADR 0011: Permissões por grupos (RBAC) como fundação da monetização

- **Status**: Aceita (decisão do usuário, 10/11/2026: vínculo treinador–aluno criado só pelo admin na primeira versão; virada sem mudança de acesso, com grupo Free vazio; visibilidade dos modelos existentes pública). A implementação é feita nos lotes 1 a 6 descritos abaixo. Este ADR registra a decisão; o lote 1 a implementa (migração 111, módulo `api/app/access`, campos `permissions` e `groups` em `/auth/me` e testes unitários). **O lote 1 está em produção desde 10/11/2026** (PR #120, promoção #122, migração 111 aplicada). **O lote 2 foi dividido em 2A a 2F** (ver Sequência de lotes). **O 2A está em produção desde 11/11/2026** (PR #124, promoção #125, deploy 42937922299; migração 112 aplicada com 0 statements): sincronização usuário→grupo, trava do último admin e smoke. Nenhum guard mudou no 2A. **O 2B está em produção desde 11/11/2026** (PR #126, promoção #128, deploy 43022098093, migração 113): ver "Lote 2B: base da troca". **O lote 2C foi dividido em 2C-1 e 2C-2.** **O 2C-1 está implementado na branch `feat/rbac-lote2c1-admin-declarado`, não publicado nem promovido** (ver "Lote 2C-1: administração com guard declarado"). O 2C-2 e os lotes 2D a 6 ainda não existem.
- **Data**: 2026-11-10

## Contexto

O MuscleFit hoje decide acesso por um papel único por usuário e por guards fixos no FastAPI. Estado do código em `C:\muscle_fit` no lote 1 (branch `feat/rbac-lote1-fundacao`, base `origin/develop` 3a27e473). Os arquivos e linhas citados foram conferidos na leitura deste ADR; os itens sem arquivo citado (JWT/authorizer ausentes, contagem de endpoints, tratamento de 403 no frontend, guarda de três rotas) vêm do levantamento do Tech Lead e devem ser reconfirmados no lote 2.

**Autenticação e identidades**

- A sessão é um token opaco em cookie httpOnly, validado em `api/app/security.py` (`get_current_identity`, linha 403). Não há JWT nem authorizer no API Gateway: toda a autorização está no FastAPI.
- Há três identidades (`Identity`, `api/app/security.py`, linha 242 em diante): sessão (`source='session'`), dispositivo (relógio, que herda o `role` e o `user_id` do dono, como na consulta de `security.py` linha 153) e token mestre (`master_token_matches`, linhas 193 a 207 e 321 a 324), que resolve sempre para o admin ativo.

**Papéis e guards**

- O papel está em `users.role`, ENUM original `('admin','regular')` (`api/db/migrations/001_schema.sql`, linha 1238), estendido para `('admin','regular','personal_trainer')` pela migração `007_workout_plan_templates.sql`. A escrita por CRUD aceita só esses três valores (`api/app/users.py`, `_ALLOWED_ROLES`, linha 48).
- Guards atuais em `api/app/security.py`:
  - `require_admin` (linha 423): exige `identity.role == 'admin'`.
  - `require_admin_or_pt` (linha 448): aceita `ADMIN_OR_PT_ROLES`.
  - `get_current_identity` (linha 403): usado no restante dos endpoints, sem checagem de papel.
- `require_admin` e `require_admin_or_pt` aparecem em cerca de 40 ocorrências em `api/app` (contagem por busca textual, que inclui importações e definições; o número exato de endpoints deve ser fechado no lote 2). São cerca de 370 endpoints em 56 arquivos, sem contagem automatizada.
- `require_admin_or_pt` cobre Modelos, tipos de treino, cardio e nutrição. Na nutrição, `resolve_target_user` (`api/app/nutrition_access.py`, linha 55) decide qual `user_id` é lido ou gravado.

**O que não existe hoje**

- Não há vínculo treinador–aluno. O personal trainer enxerga e define dados de qualquer usuário ativo. O próprio módulo `nutrition_access.py` (linhas 9 e 10) diz que isso é provisório e que o vínculo formal é planejado.
- "Ver dados de outro usuário" existe só na nutrição, pelo parâmetro `?user_id=`. Os demais domínios usam sempre o usuário logado.
- Modelos de treino (`workout_plan_templates`) têm `created_by_user_id`, mas não têm filtro por dono nem visibilidade.
- `GET /auth/me` (`api/app/auth.py`, handler `me` na linha 440) devolve `role` e `features` (linha 455, flags como `exercise_cards`), sem lista de permissões.

**Frontend**

- O menu é uma lista central em `frontend/src/lib/nav-items.ts`. Ele fica visível enquanto `/auth/me` carrega ou falha.
- Só três rotas têm guarda no cliente. Não há tratamento geral de 403.

**IA assíncrona**

- O guard roda no api-core, antes do enfileiramento do job. O worker confia no `user_id` que vem no evento.

**Motores**

- Cotas de IA de nutrição já têm dono: `api/app/domain/nutrition/ai_usage.py` (cota diária por feature, `ai_usage_log`).

**Restrições**

- A operação é de produção com usuários reais. Qualquer troca de guard pode bloquear alguém que hoje tem acesso, por isso a virada precisa preservar o comportamento.
- O projeto não tem cobrança. Há necessidade de separar "o que cada pessoa pode fazer" de "o que o plano dela inclui", sem decidir ainda o preço.
- O repositório é público (`dougbrsp/muscle_fit_v2`) e o CI tem economia de minutos. A guarda de cobertura precisa ser unitária, sem integração.

## Alternativas consideradas

1. **Papéis fixos em ENUM, com mais valores** (ex.: `free`, `pago`, `pt_pago`). Descartada: cada novo papel exige migração de ENUM, e combinações (quem é PT e também tem plano pago, ou quem é aluno de dois treinadores) não cabem em um valor só. Além disso, o acesso fica acoplado ao nome do papel, não ao que ele permite.
2. **Permissões só no frontend** (esconder menu e botões). Descartada: a API continua aberta a qualquer chamada direta. Esconder é conveniência de tela, não controle de acesso.
3. **Regras de negação** (conceder tudo e tirar com "proibido"). Descartada: a combinação de negações entre grupos é difícil de auditar e torna "o que este usuário pode" uma conta com exceções. Só concessões aditivas, com união entre grupos, são mais simples de explicar e de testar.
4. **JWT com claims de permissão** (token assinado com as permissões). Descartada nesta fase: a sessão atual é opaca e revogável no banco. Trocar para JWT mudaria o modelo de revogação e a autenticação, fora do escopo. A consulta de permissões por requisição é o custo aceito (ver Consequências).
5. **Authorizer no API Gateway**. Descartada: a autorização fica no FastAPI, perto dos dados, e o gateway não conhece a sessão nem o escopo por aluno.

## Decisão

Autorização por **grupos de permissão**, com concessões **aditivas** e **negado por padrão**.

- **Concessão** = recurso × ação × escopo.
  - Ações: `ver`, `editar`, `excluir`.
  - Escopos: `próprios` (dados do próprio usuário), `alunos` (usuários com vínculo em `trainer_students`), `todos`.
- **Catálogo de recursos** no código, em um módulo dono novo `api/app/access/`, com constantes nomeadas (Python puro). O banco guarda só grupos, concessões e membros. A tela de grupos lê o catálogo pela API, sem repetir a lista.
- **Aditivo**: um usuário pode estar em vários grupos, e vale a união das concessões. Não há regra de negação.
- **Tabelas** (migração 111):
  - `access_groups`: grupos, com flag de sistema.
  - `access_group_grants`: concessões de cada grupo.
  - `user_access_groups`: membros.
  - `trainer_students`: vínculo treinador–aluno.
  - `access_audit_log`: auditoria de alterações de grupos e membros.
- **Aplicação no servidor**: dependência `require_permission(recurso, ação)` e `authorize_target(...)`. O `authorize_target` resolve o escopo do alvo e vai substituir `resolve_target_user`. Ele não substitui `resolve_plan_owner`, que segue respondendo 404 (ver Pré-condições do lote 2).
- **Guarda de cobertura**: teste unitário que enumera todas as rotas do FastAPI e falha se alguma não declarar permissão. Uma allowlist de exceções que só encolhe. Ela lista todas as rotas que ainda não declaram `require_permission` (361 de dívida e 6 públicas), inclusive as que só usam `get_current_identity`, e o lote 2 zera a dívida. Limitações conhecidas: a guarda mede a presença de `require_permission`, não se o par recurso/ação escolhido é o adequado para a rota; o teste que garante só dois apps FastAPI varre apenas `api/app/*.py` de primeiro nível (`test_access_guarda_cobertura_rotas.py`, linha 172), então um app em subpacote escaparia; e um router cujo import falhe em `main.py` some da enumeração sem alarme (o `try/except Exception` vira log e a rota some), a menos que as rotas dele estejam na allowlist. O teto da dívida fica no mesmo arquivo que a lista (`api/tests/unit/access_cobertura_allowlist.json`), então qualquer aumento do teto exige revisão humana.
- **Grupo Admin de sistema**: com acesso total. Ele passa a ser imutável pelo serviço no lote 3; hoje não há escrita de grupo nem trava no banco, então a imutabilidade é desenho, não algo entregue. O acesso total exige o grupo com chave `admin` **e** `is_system = 1`; a checagem é feita pelo código, não só pelo nome. Até o lote 1 não existia nenhuma escrita de grupo, membro ou vínculo: o módulo `access` só lia. Desde o 2A, `api/app/access/membership.py` grava `user_access_groups` (só membros; grupos e vínculos continuam sem escrita até o lote 3 e o lote 5). Trava do último admin, com prazos distintos: (1) até o lote 1 não havia escrita de grupo, membro ou vínculo, logo não havia trava; (2) a trava sobre `users` é **pré-condição do lote 2** e tem de existir antes de qualquer guard passar a decidir por grupo, porque a partir daí rebaixar ou desativar o último admin deixaria a instalação sem admin de sessão (o acesso total vem do grupo `admin`, sem fallback para `users.role`); (3) a trava para **remoção de membro do grupo `admin` pela tela/API de grupos** entra no lote 3, junto com as mutações de grupo e membro e com a gravação em `access_audit_log`. Redação única da trava do lote 2 (a mesma em todas as seções): ela cobre três entradas, `PUT /users/{id}` trocando o papel, `PUT /users/{id}` com `is_active = false` e `DELETE /users/{id}`, inclusive quando o admin age sobre si mesmo; "último admin" conta só usuários **ativos** (`users.is_active = 1`) associados ao grupo `admin` de sistema, porque a exclusão é lógica e contar só por `user_access_groups` contaria o usuário desativado. No lote 3 o serviço também deve impedir renomear a chave `admin`, alterar `is_system` ou apagar grupos de sistema, e não apenas a tela (hoje não há trava no banco para isso).
- **Associação e exclusão de usuário**: `DELETE /users/{id}` é exclusão lógica (`UPDATE users SET is_active = 0`, `api/app/users.py`, linha 497) e não apaga a linha. A associação em `user_access_groups` permanece. O `ON DELETE CASCADE` da FK só atuaria num `DELETE` físico de `users`, que nenhum fluxo da API faz hoje. `PUT /users/{id}` também desativa (`is_active`, linha 459) e troca o papel (`role`, linha 458), e até o lote 1 não tinha a checagem contra autodesativação que o `DELETE` tem (linha 492): até o lote 1 um admin podia se rebaixar ou se desativar pelo `PUT`. Desde o 2A, `PUT /users/{id}` recusa a autodesativação com 400 (`api/app/users.py`, `update_user`). Por isso a trava do último admin (redação única acima) cobre as três entradas sobre `users` no lote 2, e a remoção de membro do grupo `admin` fica no lote 3.
- **Identidades**: o token mestre continua resolvendo para admin. O dispositivo herda as permissões do dono.
- **Virada sem mudança de comportamento** (lote 1):
  - Três grupos de sistema espelham os guards atuais: Admin, Personal Trainer e Usuário.
  - Os membros são preenchidos a partir de `users.role`.
  - Decisão do usuário (10/11/2026): os usuários atuais mantêm o acesso que têm hoje, inclusive IA e insights.
  - O grupo Free é criado vazio, sem concessões e sem membros.
  - A coluna `users.role` é mantida durante a transição. Ela continua sendo gravada e lida pelos caminhos antigos até o lote 2 terminar.
- **Vínculo treinador–aluno** (decisão do usuário, 10/11/2026): na primeira versão só o admin cria e remove vínculos. Os personal trainers deixam de ver todos os usuários e passam a ver só os vinculados. Esse efeito fica para o lote 5. Convite com aceite do aluno está fora do escopo.
- **Modelos de treino** (decisão do usuário, 10/11/2026): os modelos existentes ficam públicos quando a visibilidade for introduzida (lote 6).
- **Frontend**:
  - `/auth/me` devolve as permissões efetivas do usuário.
  - Um hook filtra menu, rotas, botões e cards de IA.
  - Enquanto a resposta de `/auth/me` não chega, tudo fica escondido.
  - No lote 4, o hook trata `null` (permissões desconhecidas) e campo ausente como "esconder", nunca como "liberar". `null` e `{}` (nenhuma permissão) são distintos no contrato, mas ambos escondem.
  - Esconder é conveniência. Quem bloqueia é o backend.
- **IA assíncrona**: a checagem de permissão continua no api-core, antes do enfileiramento. O worker não reavalia permissão e confia no evento, como hoje.
- **Motores**: bloquear IA e insights é controle de acesso, não regra de motor. Nenhuma regra de motor muda, e `docs/motores/FONTES_DA_VERDADE.md` não é alterado por este ADR. Se uma cota de IA por grupo for criada depois, ela entra no dono atual, `api/app/domain/nutrition/ai_usage.py`, e não em um módulo novo de permissões.

### Sequência de lotes

1. **Fundação** (lote 1, em produção desde 10/11/2026): este ADR, migração 111, módulo `api/app/access/`, permissões em `/auth/me` e guarda de cobertura. Sem mudança de comportamento.
2. **Aplicação no backend**: troca dos guards por `require_permission` e `authorize_target`. Allowlist zerada. Dividida em:
   - **2A Pré-condições** (em produção desde 11/11/2026): sincronização usuário→grupo, migração 112, trava do último admin, smoke. Nenhum guard mudou.
   - **2B Base da troca** (em produção desde 11/11/2026): recurso `conta.dispositivos` com migração 113; categoria "só autenticação" na guarda de cobertura; retrato congelado dos guards antigos e teste de paridade; guarda estática de `identity.role`; `authorize_self_or_all` e id estrito; `require_additional_permission` (`admin.grupos` em qualquer escrita sobre admin ou que torne alguém admin); trava com nova ordem de locks; conversão de 18 rotas de `users`, `auth`, `devices` e `jobs` (9 com `require_permission`, 9 só autenticação). Os recursos de intervals sync, aplicação de recomendação de cardio e administração de academias entram no catálogo com os lotes 2C a 2F, quando as rotas que os usam forem convertidas.
   - **2C-1** Administração com guard declarado (branch `feat/rbac-lote2c1-admin-declarado`, não publicado): 34 rotas, recurso `modelos.tipos_treino` e migração 114.
   - **2C-2** Academias e catálogos de exercícios: 72 rotas de classe `identidade_no_corpo`, com caso manual de paridade.
   - **2D** Nutrição (`resolve_target_user` → `authorize_target`, 404 preservado).
   - **2E** Analytics e engines.
   - **2F** Treino, corpo e fecho (allowlist zerada, remoção de `require_admin` e `require_admin_or_pt`).
3. **Tela "Grupos e acessos"**: API de grupos, membros e auditoria.
4. **Ocultação no frontend**: hook de permissões nas telas.
5. **Vínculo treinador–aluno e leitura de dados dos alunos**: efeito de a PT deixar de ver todos os usuários.
6. **Modelos**: visibilidade e edição pelo dono.

### Pré-condições do lote 2

Riscos levantados na revisão independente. O lote 2 só troca um guard depois de cobrir cada item abaixo.

**Estado do 2A** (em produção desde 11/11/2026). Os itens abaixo são as pré-condições; o que o 2A entrega está marcado, o que o 2B cobre está marcado na lista de pendências, e o restante segue pendente para 2C–2F.

- [x] **Sincronização (a)**: a 112 reconcilia os grupos de sistema com `users.role` para todos os usuários, não só para quem não tem linha (ver a decisão abaixo). Reexecutar a 111 continua não sendo o caminho.
- [x] **Sincronização (b)**: migração 112 implementada em `api/db/migrations/112_user_access_groups_backfill.py`, com a decisão sobre papel alterado depois da 111.
- [x] **Sincronização (c)**: a associação é gravada na mesma transação do `INSERT`/`UPDATE` de `users` em `POST /users` (`api/app/users.py`, `assign_role_group`), na troca de papel de `PUT /users/{id}` (`switch_role_group`, que remove o grupo de sistema antigo) e no seed do admin em `api/app/auth.py` (`ensure_admin_seed`, `assign_role_group`). Ainda não cobre: `scripts/prod_to_dev.py`, restauração por dump, fixtures de `api/tests/integration`, e o smoke tratado abaixo.
- [x] **Sincronização (d)**: `api/tests/unit/test_access_sincronizacao_grupo.py` cobre os dois casos (usuário criado depois da 111 recebe o grupo do papel; troca de papel não deixa o grupo antigo), com banco falso. O teste de paridade completo (guards antigos × novos, três identidades) continua para o 2B.
- [x] **Trava do último admin**: `api/app/access/membership.py` (`ensure_not_last_admin`, `lock_active_admin_ids`) é chamada em `PUT /users/{id}` (quando `is_active = false` ou quando o papel muda para um valor diferente de `admin`) e em `DELETE /users/{id}`, com resposta 409 `last_admin`. A troca de papel para `admin` não avalia a trava, porque não reduz o número de admins.
  - **Ordem de locks** (atualizada no 2B; igual nas duas rotas, `api/app/users.py`, `update_user` e `deactivate_user`): (1) admins ativos, por `lock_active_admin_ids` (`SELECT ... FOR UPDATE` com JOIN sobre `users`, `user_access_groups` e `access_groups`, `ORDER BY u.id`), no `PUT` só quando ele pode tirar um admin (desativação ou troca de papel que não seja para `admin`) e sempre no `DELETE`; (2) a linha do alvo, com `SELECT id, role FROM users WHERE id = %s FOR UPDATE` no `PUT` e `SELECT id FROM users WHERE id = %s FOR UPDATE` no `DELETE`; (3) a decisão da trava (`ensure_not_last_admin`, com `locked_admin_ids`); (4) só então a escrita em `users` e, se houver, a troca de grupo. No 2A a ordem era a inversa (alvo antes dos admins); a mudança é do 2B.
  - O papel usado para decidir a troca de grupo e a avaliação da trava é o lido sob o lock do alvo. Motivo: dois `PUT` concorrentes podiam deixar `users.role` e o grupo do mesmo usuário divergentes.
  - Custo: toda desativação e todo rebaixamento travam todas as linhas de admin durante a transação. O volume é baixo, porque as rotas são só de admin.
  - Deadlock entre admins: ver "Locks e deadlock entre admins" nas decisões do 2A (alternativa (b) adotada no 2B).
  - `api/tests/unit/test_users_trava_ultimo_admin.py` verifica com banco falso a ordem dos statements. **Não há teste de concorrência com transações reais**; a atomicidade e a ordem dos locks estão provadas só pelos statements emitidos.
- [x] **Smoke** (`scripts/smoke-async-jobs.sh`, bloco `smoke-admin`, linhas 98 a 104): o `INSERT` em `users` só roda se não houver admin ativo; logo depois, um `INSERT IGNORE INTO user_access_groups` associa ao grupo `admin` o usuário `smoke-admin` filtrado por `u.role = 'admin' AND u.is_active = 1`. É SQL de script, não código da aplicação. Diferença em relação ao código: não há `START TRANSACTION` no bloco e o cliente `mysql` roda em autocommit por padrão, então os dois statements não estão na mesma transação; e o bloco roda com `2>/dev/null`, então uma falha fica silenciosa.
- [x] **Coberto no 2B**: exigir `admin.grupos` para qualquer escrita sobre alvo admin ou que torne alguém admin (criar, promover, rebaixar, editar, reativar, desativar, excluir, resetar senha), com teste negativo; guarda estática de `identity.role` fora de `app/access` (ratchet em `access_identity_role_allowlist.json`); id estrito em `authorize_target` e `authorize_self_or_all`; paridade guard antigo × permissão nova para as 18 rotas convertidas (ver "Lote 2B: base da troca").
- [ ] **Pendente para 2C–2F**: checagens de papel em handlers dos demais módulos (ex.: `gyms.py`, `exercise_catalog_admin.py`); as 72 rotas `identidade_no_corpo` e as 3 `outra` do retrato, sem decisão automática; posse da linha além de jobs e fotos; 404 que esconde existência; concessões largas demais em leitura; matriz mais larga que alguns guards (intervals sync, aplicar recomendação de cardio, academias); paridade das demais rotas.

- **Sincronização usuário→grupo.** Até o lote 1, nenhum ponto que escreve em `users` grava `user_access_groups` (desde o 2A, os três pontos marcados abaixo gravam; os demais continuam sem gravar). Busca por `INSERT INTO users` feita em `api/app`, `api/scripts`, `api/db`, `scripts/`, `tools/`, `dev/` e `localstack/` (arquivos `.py`, `.sh`, `.sql`, `.json`, `.yml`, `.ps1`). Pontos encontrados:
  - `api/app/users.py`, `POST /users` (linha 322) e a troca de papel em `PUT /users/{id}`;
  - `api/app/auth.py`, seed do admin no boot (linha 202): em ambiente novo ou restaurado esse admin ficaria sem grupo e receberia 403 em tudo depois do lote 2;
  - `scripts/smoke-async-jobs.sh` (linha 98): usuário de smoke criado por SQL; deve gravar a associação, ou o smoke passa a levar 403;
  - `tools/onprem-sync/backups/*/restore.sql`: dumps históricos de restauração, não código de criação de usuário; restaurar um dump antigo deixa usuários sem grupo e exige rodar a 112 depois;
  - `scripts/prod_to_dev.py` (cópia de dados de produção para o DEV) e a restauração por dump: criam ou substituem linhas de `users` sem gravar `user_access_groups`; entram na mesma lista de caminhos que o lote 2 precisa cobrir (a regra a garantir é que o usuário copiado ou restaurado nunca fique sem grupo). O script tem ainda dois bloqueios próprios, descritos em "Impacto em produção";
  - `api/scripts`, `api/db`, `dev/` e `localstack/`: sem ocorrência;
  - fixtures de `api/tests/integration` (fora da busca acima) inserem usuários por SQL e precisam do mesmo cuidado quando passarem a exercitar rotas com `require_permission`.

  O lote 2 cobre estes itens antes de trocar qualquer guard:
  - (a) A migração 111 NÃO refaz o preenchimento. O seed de membros roda só com a tabela `user_access_groups` vazia. Usuário criado, ou com papel alterado, depois da 111 fica sem grupo (negado) ou no grupo do papel antigo (acesso indevido). Reexecutar a 111 não corrige isso, porque depois que a tabela tem linhas o conteúdo é do admin.
  - (b) O lote 2 precisa de uma migração nova, `112`, idempotente, que insere a associação só para usuários sem nenhuma linha em `user_access_groups`. Ela é aplicada imediatamente antes de ativar os guards novos. A 112 não corrige quem já tem linha e teve o papel alterado depois da 111; esse caso precisa de decisão antes da ativação. *(Decidido no 2A: a 112 reconcilia também esse caso; ver "Decisões e pendências do 2A".)*
  - (c) Todo ponto que cria usuário (`POST /users`, seed do admin em `auth.py` e os demais da lista acima) grava a associação na mesma transação do `INSERT INTO users`. A troca de papel **remove** a associação do grupo de sistema antigo e insere a nova, na mesma transação da alteração de `users.role`: sem a remoção, um ex-admin ou ex-PT acumularia grupos e a união manteria o acesso antigo.
  - (d) O teste de paridade do lote 2 ganha o caso "usuário criado depois da 111 tem grupo" (e o caso "troca de papel não deixa o grupo antigo").
- **Trava do último admin.** Antes de qualquer guard decidir por grupo, a trava cobre três entradas: `PUT /users/{id}` trocando o papel, `PUT /users/{id}` com `is_active = false` e `DELETE /users/{id}`, inclusive quando o admin age sobre si mesmo (até o lote 1 só o `DELETE` recusava a autodesativação; desde o 2A o `PUT` também recusa, com 400). "Último admin" conta só usuários **ativos** (`users.is_active = 1`) associados ao grupo `admin` de sistema; como a exclusão é lógica, contar só por `user_access_groups` contaria o desativado. Sem a trava, rebaixar ou desativar o último admin deixa a instalação sem admin de sessão, porque o acesso total vem do grupo `admin`, sem fallback para `users.role`. A trava de remoção de membro do grupo `admin` pela tela/API de grupos é do lote 3.
- **Deploy em todas as imagens.** api-core, engines e as Lambdas de IA resolvem permissões de forma independente e dependem do mesmo schema, aplicado só pela Lambda de migrações; todas precisam subir com a 111 já aplicada.
- **Matriz mais larga que alguns guards.** `POST /integrations/intervals/sync` (hoje só admin) e `POST /cardio-recommendations/{id}/apply` (hoje admin ou PT) precisam de recurso próprio no mapeamento rota→permissão, com teste que compara por papel com o guard antigo.
- **Checagens de papel dentro de handlers e de SQL.** Há `identity.role == 'admin'`, `is_admin(`, `is_staff(` e `_require_admin_identity` em `gyms.py`, `exercise_catalog_admin.py`, `exercise_mapping_catalog.py`, `muscle_volume_classes.py`, `exercise_cards.py`, `nutrition_access.py` e em serviços. A guarda de cobertura não enxerga essas checagens. O lote 2 precisa cobri-las e ganhar uma guarda estática que proíba `identity.role` novo fora de `security.py` e do módulo `access`.
- **Rotas sem `get_current_identity`.** Fotos e logos por id, e rotas com `resolve_identity_from_credentials`, precisam ser classificadas uma a uma.
- **Concessões largas demais em leitura.** A matriz concede as três ações (`ver`, `editar`, `excluir`) em recursos que são só de leitura (dashboards, relatórios, progressão, IA). Reduzir conforme o mapeamento real das rotas.
- **Posse da linha.** A permissão por recurso não prova que o registro pertence ao usuário. Rotas por id (apagar ou editar uma atividade, um plano, uma foto) precisam checar o dono da linha além da permissão. `authorize_target` resolve o usuário alvo, não a posse de uma linha.
- **404 que esconde existência.** Rotas que hoje respondem 404 para não revelar que um recurso alheio existe (ex.: `resolve_plan_owner` em `api/app/nutrition_access.py`, linha 46) não podem passar a responder 403. `authorize_target` responde 403 e substitui `resolve_target_user`, não `resolve_plan_owner`.
- **Ordem de deploy do lote 2.** Quando as rotas exigirem permissão, um banco sem a 111 responde 503 em tudo. O lote 2 só sobe com a 111 já aplicada e conferida em produção.
- **Paridade.** Teste por papel antigo × novo para todas as rotas e para os três tipos de identidade (sessão, dispositivo, token mestre). Decisão explícita sobre usuário desativado e sobre papel fora dos três valores (`admin`, `personal_trainer`, `regular`); hoje esse caso fica sem grupo, portanto negado.

#### Decisões e pendências do 2A

**Papel alterado depois da 111 (decisão, 2A).** A 112 reconcilia os grupos de sistema (`access_groups.is_system = 1`: `admin`, `personal_trainer`, `usuario`) com `users.role`:
- remove a associação a grupo de sistema que não corresponde ao papel atual e insere a correta;
- grupos que não são de sistema (ex.: `free`, grupos criados pelo admin) ficam intactos;
- papel fora dos três valores fica sem grupo de sistema (negado);
- inclui usuários inativos, porque o papel deles volta a valer se forem reativados.

Motivo: `users.role` é a verdade até o fim do lote 2, e a 112 alinha os grupos a ela. A 112 é só DML, sem DDL e sem FK; o `plan` lê antes de escrever e emite um statement por usuário e grupo, então reexecutar converge. `down` não apaga nada, porque não há como distinguir as associações da 112 das demais.

**Consequência a registrar.** Depois do lote 3 (edição de membros pela tela), a 112 não deve ser reexecutada como ferramenta de correção, porque desfaria ajustes manuais feitos em grupos de sistema.

**Limitação entre a 112 e o código novo (aberta, não afeta o 2A).** A 112 só é aplicada pelo `migrate` da promoção. Usuário criado pelo código do lote 1 entre esse `migrate` e a atualização das Lambdas da aplicação fica sem grupo, e a 112 não o corrige sozinha. Nenhum guard lê grupo no 2A, então isso não causa negação agora. Mas antes de ativar os guards do 2B, a conferência por `SELECT` precisa ser repetida; a correção é uma nova reconciliação decidida pelo Tech Lead, não uma reexecução automática.

**Trava do último admin conta só por grupo (limitação aceita, 2A).** `lock_active_admin_ids` conta admins ativos com linha em `user_access_groups`. Antes da 112, um admin que existe só por `users.role` (sem linha no grupo `admin`) não é contado: a trava não dispara para ele, ou seja, ele pode ser desativado ou rebaixado sem 409, e não conta como admin restante, então a desativação do último admin contado responde 409 mesmo quando esse outro admin existe. Para o último admin contado, a trava continua decidindo do lado seguro: o bloqueio pode ser excessivo, mas nunca remove o último admin contado. A ordem do deploy (migrate da 112 antes da atualização das Lambdas) fecha a janela dos usuários já existentes. Resta a janela entre o migrate e a atualização das Lambdas, em que o código do lote 1 ainda pode criar um admin sem grupo; ela já é a limitação acima. Alternativa não adotada: contar também `users.role = 'admin'` na trava. Não foi adotada porque reintroduz a segunda fonte de verdade durante a transição, e a regra do lote 2 é que o acesso total vem só do grupo `admin`, sem fallback para `users.role`.

**Locks e deadlock entre admins (limitação aceita, 2A).** A ordem alvo → admins ativos não é uma ordem total. Dois admins que agem ao mesmo tempo sobre alvos diferentes travam, cada um, a linha do próprio alvo e depois as linhas dos admins ativos; essas duas etapas podem se cruzar e formar um ciclo. O InnoDB aborta uma das transações com erro 1213. Não há tratamento desse erro no código de `api/app` (busca sem ocorrência), então a resposta é 5xx em vez de 409. O estado fica consistente, porque a transação abortada sofre rollback. Alternativas não adotadas no 2A: (a) mapear 1213 para 409 ou 503, com retentativa; (b) travar os admins ativos em ordem determinística antes do alvo. **Decidido no 2B: alternativa (b)**, com a ordem de locks descrita em "Ordem de locks" acima e no "Lote 2B". A alternativa (a) segue pendente. O caminho residual da releitura da trava (ver "Lote 2B") ainda pode inverter a ordem, então a prova de ausência de ciclo depende de teste de concorrência, que não existe.

**Pendências que o 2A não cobre** (seguem valendo):
- Exigir `admin.grupos` para qualquer escrita sobre admin ou que torne alguém admin: **feito no 2B** (`require_additional_permission` em `POST /users`, `PUT /users/{id}`, `DELETE /users/{id}` e `POST /users/{id}/reset-password`, com teste negativo em `api/tests/unit/test_users_permissoes_rbac.py`).
- `scripts/prod_to_dev.py` e restauração por dump. Desde o PR #121, `verify_snapshot` (`scripts/prod_to_dev.py`, linhas 817 a 823) aborta a cópia se o usuário copiado não tiver nenhuma linha válida em `user_access_groups`. Restaurar um dump antigo deixa usuários sem grupo e exige rodar a 112 depois.
- Fixtures de `api/tests/integration` e de `api/tests/support` que inserem usuários por SQL sem grupo (ex.: `test_seg06_seg07_sessoes_e_login.py`, linha 37). Tratado em parte no 2B: `api/tests/integration/conftest.py` ganhou `conceder_grupo_do_papel` e a fixture `conceder_grupo`, e a fixture `catalog` concede o grupo do `teste-admin`. Os demais testes que criam usuário por SQL e chamam rota com permissão devem usar o helper (regra no Lote 2B).

**Dependência da 111 na trava.** A trava lê `access_groups` e `user_access_groups` também em `DELETE /users/{id}` e na desativação por `PUT`, mesmo sem troca de papel. Sem a 111 aplicada, essas rotas respondem 500.

### Lote 2B: base da troca

Branch `feat/rbac-lote2b-base-da-troca` (commit `4d8c467f`, merge `157184b5`). Em produção desde 11/11/2026 (PR #126, promoção #128, deploy 43022098093). O 2B converte 18 rotas de `users`, `auth`, `devices` e `jobs`, acrescenta o recurso `conta.dispositivos` (migração 113) e cria as provas da troca: retrato dos guards antigos, teste de paridade, guarda estática de papel e a categoria "só autenticação". A migração 112 não foi alterada.

**Rotas convertidas (18)**

| Rota | Guard antigo (retrato) | Regra nova |
|---|---|---|
| `GET /users` | `require_admin` | `admin.usuarios`/`ver` |
| `POST /users` | `require_admin` | `admin.usuarios`/`editar`; com `role = admin`, também `admin.grupos`/`editar` |
| `PUT /users/{id}` | `require_admin` | `admin.usuarios`/`editar`; se o alvo é admin ou o papel pedido é `admin`, exige também `admin.grupos`/`editar` (qualquer campo, inclusive desativar e reativar) |
| `DELETE /users/{id}` | `require_admin` | `admin.usuarios`/`excluir` (exclusão lógica); se o alvo é admin, exige também `admin.grupos`/`editar` |
| `POST /users/{id}/reset-password` | `require_admin` | `admin.usuarios`/`editar`; se o alvo é admin, exige também `admin.grupos`/`editar` |
| `GET /users/me` | sessão | só autenticação |
| `GET /users/me/hr-zones` | sessão | só autenticação |
| `PUT /users/me` | sessão | só autenticação |
| `POST /users/{id}/photo` | `autenticado` (com checagem de papel no retrato) | só autenticação; terceiro exige `admin.usuarios`/`editar` |
| `GET /users/{id}/photo` | `autenticado` (com checagem de papel no retrato) | só autenticação; terceiro exige `admin.usuarios`/`ver` |
| `DELETE /users/{id}/photo` | `autenticado` (com checagem de papel no retrato) | só autenticação; terceiro exige `admin.usuarios`/`excluir` |
| `GET /auth/me` | `autenticado` | só autenticação |
| `POST /auth/change-password` | `autenticado` | só autenticação |
| `GET /jobs/{job_id}` | `autenticado` | só autenticação; posse checada no handler (`owner_user_id`, M65) |
| `POST /devices/pair` | usuário sem dispositivo | `conta.dispositivos`/`editar` |
| `GET /devices` | usuário sem dispositivo | `conta.dispositivos`/`ver` |
| `GET /devices/me` | dispositivo | `conta.dispositivos`/`ver`, pela concessão do dono |
| `DELETE /devices/{id}` | usuário sem dispositivo | `conta.dispositivos`/`excluir` |

Nas rotas de dispositivo, a recusa de token de dispositivo (403 de `require_user_credential`, ou de `require_device_credential` em `GET /devices/me`) continua vindo antes da consulta de permissões.

**Categoria "só autenticação"**

- Critério: a rota só mexe na conta do próprio usuário autenticado (perfil e troca de senha), ou a posse da linha é checada pelo dono (`GET /jobs/{job_id}`), ou é foto de perfil em que o próprio passa e terceiro cai em `authorize_self_or_all`.
- Não é "qualquer identidade". O perfil (`/users/me`, `/users/me/hr-zones`) mantém `require_session_identity`, então dispositivo e token mestre continuam com 403. A categoria aceita o tipo de credencial que o retrato já exigia.
- Pino: a lista `so_autenticacao` de `api/tests/unit/access_cobertura_allowlist.json` (9 rotas) e `SO_AUTENTICACAO_APROVADAS` em `api/tests/unit/test_access_guarda_cobertura_rotas.py`. Rota nova nessa categoria exige revisão de segurança e alteração explícita do pino. Ela não pode declarar permissão nem estar em `divida` ou `publicas`.
- Dívida: `divida.teto` passou de 361 para 343, porque as 18 rotas saíram da lista. Conta: 343 em dívida + 6 públicas + 9 só autenticação + 9 com `require_permission` = 367 rotas do retrato.

**Recurso `conta.dispositivos` e migração 113**

- `api/app/access/catalog.py`: recurso `conta.dispositivos`, rótulo "Relógio e dispositivos", domínio `Conta`. É o único recurso novo do 2B. A matriz dá `ver`, `editar` e `excluir` no escopo `proprios` aos grupos `usuario` e `personal_trainer`. `free` continua vazio, e `admin` segue com acesso total implícito.
- `api/db/migrations/113_access_grants_conta_dispositivos.py` faz só DML em `access_group_grants`: sem DDL e sem FK nova. Insere até 6 linhas (2 grupos × 3 ações), com `INSERT IGNORE` só do que falta. Concessão já existente, mesmo com escopo editado pelo admin, não é tocada. Grupos que não são de sistema com a mesma chave também não são tocados.
- Sem as tabelas da 111, o plano é vazio, com log e sem erro. Se `usuario` ou `personal_trainer` não existir como grupo de sistema (`is_system = 1`), levanta `AccessGrantsContaDispositivosError` antes de qualquer escrita.
- Reexecutar converge: sobre o estado já convergido o plano é vazio. O `down` remove só as linhas de `conta.dispositivos` dos dois grupos. Em produção o runner não roda `down`.
- Paridade entre migração e catálogo: `api/tests/unit/test_migracao_113_concessoes.py` (banco falso).

**Retrato congelado dos guards antigos**

- `api/tests/support/access_retrato.py` classifica cada rota viva (367, pelo par método e caminho, nos dois apps) pelo guard antigo lido da árvore de dependências e do corpo, e grava `api/tests/unit/data/access_retrato_guards_antigos.json`. Regenerar só de propósito: `cd api && python -m tests.support.access_retrato`. O teste nunca regenera o JSON.
- Contagem por classe no JSON: `autenticado` 217, `identidade_no_corpo` 72, `admin_ou_pt` 36, `admin` 29, `publica` 3, `outra` 3, `usuario_sem_dispositivo` 3, `sessao` 3, `dispositivo` 1 (soma 367). As `outra` são `GET /health`, `GET /ready` e `POST /auth/logout`; as `publica` são o login e as duas rotas de esqueci a senha.
- Limites (docstring do módulo): a análise segue chamadas a funções do mesmo módulo por até dois níveis e, de outros módulos `app.*`, só helpers cujo nome começa com `_require`. Não segue métodos, lambdas, chamadas dinâmicas nem checagem de papel feita por SQL. O retrato reflete o código antes do 2B.
- `api/tests/unit/test_access_retrato_guards.py` confere rotas novas sem retrato, rotas do retrato que sumiram (que precisam estar em `rotas_removidas_depois_do_retrato`), rota em dívida cuja classificação mudou sem conversão, e a tabela de decisão contra os guards reais.

**Teste de paridade**

- `api/tests/unit/test_access_paridade_guards.py` compara, para as 9 rotas com permissão e as 9 de só autenticação, e para papel (`admin`, `personal_trainer`, `regular`) × identidade (`session`, `device`, token mestre, este só com `admin`): a decisão do guard antigo contra a nova. A nova é o tipo de credencial exigido pela classe mais `has_permission` de todas as permissões declaradas, sobre `DEFAULT_GRANTS_BY_GROUP` do grupo do papel.
- Não prova: checagens dentro do handler (papel, `authorize_self_or_all`, `require_additional_permission`, trava do último admin), que ficam para os testes HTTP de cada módulo; posse da linha e escopo (`has_permission` aceita qualquer escopo); que o banco tenha as concessões do catálogo (isso é `test_migracao_113_concessoes.py`); rotas não convertidas.
- As 72 rotas `identidade_no_corpo` e as 3 `outra` não têm decisão automática. Se uma delas for convertida sem caso manual em `CASOS_MANUAIS` (vazio no 2B), o teste falha. Elas exigem caso manual provado à mão nos lotes 2C a 2F, antes da conversão.

**Guarda estática de papel**

- `api/tests/unit/test_access_guarda_identity_role.py` lê por AST `api/app/**/*.py`, exceto `app/security.py` e `app/access/`, e conta por arquivo os acessos `.role` em objetos de identidade e as chamadas ou definições de `is_admin`, `is_staff`, `is_admin_or_pt` e `_require_admin_identity`.
- Ratchet em `api/tests/unit/access_identity_role_allowlist.json`: arquivo novo com ocorrência falha; contagem acima do registrado falha; contagem abaixo pede para baixar o número. O teto é a soma (63, em 11 arquivos). `users.py` tem zero e não aparece.
- Pontos cegos (docstring do teste): alias (`me = identity`), `getattr(identity, "role")`, `row["role"]`, filtro de papel em SQL, guard passado por referência sem chamada, e objeto com outro nome não anotado.

**Id estrito e helpers de alvo**

- `validate_target_user_id` (`api/app/access/service.py`) aceita `None` quando o id não é obrigatório, ou `int > 0`. `bool`, `float`, `str` e `int <= 0` levantam 400 `invalid_target` antes de qualquer consulta. Não há coerção: `"10"` não vira 10.
- `authorize_target` usa esse validador. Ainda não tem chamador em `api/app`; `resolve_target_user` segue em uso até o 2D.
- `authorize_self_or_all(identity, recurso, acao, target_user_id)`, no mesmo arquivo e usado nas três rotas de foto: id obrigatório e estrito. Alvo igual ao próprio `user_id` passa sem consulta. Alvo de terceiro exige escopo `todos`; `alunos` não basta, porque a função não consulta vínculo. Qualquer outro caso é 403.
- `require_additional_permission(request, identity, recurso, acao)` (`api/app/access/deps.py`) exige, dentro do handler, uma concessão além da declarada na rota. Reusa o cache por requisição de `permissions_for_request`.

**Admin: escritas sobre alvo admin (regra única)**

- Qualquer escrita das rotas administrativas de `/users` sobre um alvo que é admin, ou que torna alguém admin, exige `admin.grupos`/`editar` além da permissão declarada na rota. Vale para: `POST /users` com `role = admin`; `PUT /users/{id}` quando o alvo é admin (qualquer campo, inclusive `is_active` true ou false e troca de papel) ou quando o papel pedido é `admin`; `DELETE /users/{id}` de admin; `POST /users/{id}/reset-password` de admin.
- "Alvo é admin" = `users.role = 'admin'` lido na mesma transação, ou id entre os admins ativos já travados, ou pertença ao grupo de sistema `admin` (`is_system_admin_member`, `api/app/access/membership.py`). Admin inativo também conta.
- Ordem das respostas: 400 de autodesativação ou auto-reset → 404 de alvo inexistente → 403 sem `admin.grupos` → 409 `last_admin` → escrita. Quem não tem `admin.grupos` recebe 403 mesmo quando o alvo é o último admin, não 409. No `POST` a checagem vem antes de qualquer escrita; nas demais rotas, depois da leitura do alvo (sob lock no `PUT` e no `DELETE`), e o 403 desfaz a transação. Código: `api/app/users.py`, `_alvo_e_admin`, `update_user`, `deactivate_user` e `reset_user_password`.
- Motivo: sem a regra, quem tivesse só `admin.usuarios` resetaria a senha de um admin, receberia a senha temporária na resposta e assumiria a conta; também editaria o e-mail ou reativaria um admin. Não era explorável com os grupos de sistema atuais (só `admin` tem `admin.usuarios`); passaria a ser com os grupos personalizados do lote 3.
- Autodesativação recusada com 400, no `PUT` e no `DELETE`.

**Trava do último admin (nova ordem de locks)**

- A ordem nas três entradas está em "Ordem de locks" (checklist do lote 2): admins ativos por id, depois a linha do alvo, depois a decisão e a escrita. Para deadlock entre admins, adotou-se a alternativa (b) do 2A. A alternativa (a) segue pendente.
- Parâmetro `locked_admin_ids` de `ensure_not_last_admin` (`api/app/access/membership.py`): ids já travados nesta transação. Com outro admin na lista, a remoção é segura. Se o alvo é o único da lista, recusa com 409 `last_admin`. Se a lista não tem o alvo nem outro admin, o alvo pode ter sido promovido enquanto a transação esperava o lock; nesse caso a função relê e trava de novo. Esse caminho residual inverte a ordem para alvo → admins, e um ciclo ainda é possível em tese.
- O papel decidido é o lido sob o lock do alvo, como no 2A.
- Prova: `api/tests/unit/test_users_trava_ultimo_admin.py`, com banco falso, verifica os statements emitidos e a ordem. **Não há teste de concorrência com transações reais**: atomicidade, ordem efetiva e deadlock não estão provados em MySQL.

### Lote 2C-1: administração com guard declarado

Branch `feat/rbac-lote2c1-admin-declarado` (não publicado, não promovido). O 2C-1 converte 34 rotas de administração: ciclo de cards de exercício, tipos de treino, regras de carga, segurança de treino, mapeamento canônico, mapeamento de exercícios e corpo de grupos musculares. A permissão é declarada por rota. Os guards de router de `training_types.py`, `training_safety_admin.py` e `exercise_mapping_canonical.py` saíram; `dashboard_modules.py` manteve o `get_current_identity` do router, porque as outras rotas do arquivo são do 2E. Nenhuma função mudou de assinatura.

**Rotas convertidas (34)**

| Módulo | Rotas | Guard antigo (retrato) | Regra nova |
|---|---|---|---|
| `exercise_cards.py` | 6 de `/exercises/{id}/card-generations` e 3 de `/exercises/{id}/card-references` | `admin` | `ia.cards_exercicio` |
| `exercise_cards.py` | `GET /exercises/{id}/card` | `autenticado` | `exercicios.catalogo`/`ver` |
| `training_types.py` | 8 de `/training-types` | `admin_ou_pt` | `modelos.tipos_treino` |
| `next_set_load_rules_admin.py` | `GET` e `PUT /draft`, `POST /publish`, `GET /versions`, `POST /describe`, `POST /simulate` | `admin` | `admin.regras_carga` |
| `next_set_load_rules_admin.py` | `GET /next-set-load-rules/published` | `autenticado` | `treino.modo_treino`/`ver` |
| `training_safety_admin.py` | `GET` e `PUT /equipment/{id}/safety`, `GET` e `PUT /equipment-exercises/{link_id}/safety` | `admin` | `admin.seguranca_treino` |
| `exercise_mapping_canonical.py` | `POST /mapping-canonical/associate` | `admin` | `admin.catalogos`/`editar` |
| `exercise_mapping_canonical.py` | `GET /mapping-canonical/options`, `GET /mapping-canonical/unmapped` | `autenticado` | `exercicios.catalogo`/`ver` |
| `main.py` | `POST /exercise-mappings` | `admin` | `admin.catalogos`/`editar` |
| `dashboard_modules.py` | `PUT /analytics/muscle-group-body-map` | `admin` | `admin.catalogos`/`editar` |

Ação por método: GET = `ver`; POST, PUT e PATCH = `editar`; DELETE = `excluir`.

**Recurso `modelos.tipos_treino` e migração 114**

- Recurso `modelos.tipos_treino` (rótulo "Tipos de treino", domínio Modelos), em `api/app/access/catalog.py`. A matriz concede `ver`, `editar` e `excluir` com escopo `todos` só a `personal_trainer`, que passa a ter 85 concessões no catálogo. `usuario` e `free` não recebem. `admin` tem acesso total implícito.
- Motivo: `/training-types` é só de admin ou PT, inclusive nos GET, e `modelos.treino`/`ver` é concedido a `usuario`.
- Migração `api/db/migrations/114_access_grants_modelos_tipos_treino.py`: só DML em `access_group_grants`, sem DDL e sem FK. Insere até 3 linhas no grupo de sistema `personal_trainer`. Se as tabelas da 111 não existem, o plano sai vazio e a migração registra log. Se o grupo de sistema falta, levanta erro próprio antes de qualquer escrita. Não toca concessão existente nem grupo que não é de sistema. Reexecutar converge.
- Diferença em relação à 113: ao fim de `up`, a migração relê as linhas e levanta erro se alguma linha planejada não existir. A releitura é pulada em dry-run e em plano vazio, e confere os pares grupo e ação, não o escopo.
- `down` remove só as linhas do recurso nesse grupo.
- Paridade catálogo × migrações agora é 111 + 113 + 114 (`api/tests/unit/test_migracao_114_concessoes.py`).

**Guarda de cobertura**

- `api/tests/unit/access_cobertura_allowlist.json`, conferida no JSON e por enumeração: dívida de 343 para 309.
- 309 em dívida + 6 públicas + 9 só autenticação + 43 com `require_permission` (9 do 2B + 34) = 367.
- Nenhuma rota entrou em `so_autenticacao` nem em `publicas`.

**Paridade (`api/tests/unit/test_access_paridade_guards.py`)**

- Prova: as 34 rotas são das classes `admin`, `admin_ou_pt` e `autenticado`, com decisão automática; `CASOS_MANUAIS` continua vazio; 43 rotas são cobertas nas 7 combinações papel × identidade.
- Não prova: checagens dentro do handler, posse da linha e escopo, nem que o banco tenha as concessões do catálogo (isso é o teste da 114).
- O retrato congelado (`api/tests/unit/data/access_retrato_guards_antigos.json`) não foi regenerado.

**Testes HTTP (`api/tests/unit/test_catalogos_admin_permissoes_rbac.py`)**

- 64 testes, 10 rotas representantes; token mestre sem consulta ao banco; falha de banco responde 503; DELETE exige `excluir`.
- Limites: o positivo de `POST /mapping-canonical/associate` é inferido por 422; `POST /exercise-mappings` é testado num app mínimo com as dependências copiadas de `app.main`; handlers de negócio são substituídos por dublês.

### Riscos para lotes futuros

- **Lote 2 (quem pode tornar alguém admin pela rota de usuários)** (coberto no 2B): com a sincronização do lote 2, `POST /users` e `PUT /users/{id}` com papel `admin` colocam o usuário no grupo `admin`. Criar usuário admin e trocar o papel para ou a partir de `admin` deve exigir a permissão de associar ao grupo admin (`admin.grupos`) além de `admin.usuarios`, com teste negativo; senão quem tiver só `admin.usuarios` vira admin por essa rota.
- **Lote 2 (atomicidade da trava do último admin)** (coberto no 2B na ordem de locks; o teste de concorrência real continua pendente): a contagem de admins ativos e a escrita acontecem na mesma transação, com bloqueio das linhas dos admins ativos (`SELECT ... FOR UPDATE` ou equivalente) e teste de concorrência; sem isso, dois admins se removem ao mesmo tempo.
- **Lote 2 (`authorize_target`)** (id estrito coberto no 2B; `authorize_target` ainda não tem chamador em `api/app`, a troca de `resolve_target_user` é do 2D): antes de substituir `resolve_target_user` (`api/app/nutrition_access.py`, linhas 55 a 61, que só faz `int(user_id)`), exigir id inteiro estrito e maior que zero. Hoje 0, negativo, booleano e float truncado passam no escopo `todos`; não concedem acesso indevido, mas o contrato é implícito.
- **Lote 3**: só quem tem `admin.grupos` pode colocar alguém no grupo `admin`; ninguém se coloca nele sozinho; `is_system` e a chave dos grupos de sistema não são editáveis por API (hoje não há trava no banco para isso).
- **Lote 2 (mapeamento rota→permissão)**: as rotas de equipamentos, de vínculo academia–equipamento e de logos em `api/app/gyms.py` são só de admin por checagem dentro do handler (`_require_admin_identity`, a partir da linha 150), enquanto a matriz dá a `usuario` editar e excluir em `treino.academias` com escopo próprio. O mapeamento precisa de um recurso administrativo para essas rotas, para não abri-las a quem tem só `treino.academias`.
- **Lote 5**: além do vínculo ativo, conferir que o treinador pertence ao grupo `personal_trainer` (`has_active_link` só olha `trainer_students`).
- **Lote 2C–2F (deadlock)**: mapear o erro 1213 para 409 ou 503 (alternativa (a) do 2A) segue pendente; sem ela, um ciclo entre admins vira 5xx.
- **`GET /training-types` no registro de treino**: `frontend/src/components/NewWorkoutModal.tsx` (linha 89) chama `GET /training-types?is_active=true`. A rota já respondia 403 a usuário comum antes do 2C-1 (guard de router `require_admin_or_pt`) e continua assim. Não foi verificado se o modal trata o erro nem se só aparece para admin e PT.
- **Lambda engines resolve permissões**: `PUT /analytics/muscle-group-body-map` vive no router de `api/app/dashboard_modules.py`, incluído só em `app.main_engines`. A partir do 2C-1, essa Lambda consulta as tabelas de acesso nessa rota.
- **Positivos de escrita provados por dublê**: ver os limites dos testes HTTP do 2C-1 (positivo de `POST /mapping-canonical/associate` inferido por 422; `POST /exercise-mappings` em app mínimo; handlers de negócio substituídos por dublês).
- **Lote 2C–2F (deadlock, PUT que promove a admin)**: `switch_role_group` insere em `user_access_groups` com FK para a linha do grupo `admin`, e essa linha entra no JOIN de `lock_active_admin_ids` (`FOR UPDATE`, `api/app/access/membership.py`). Um `PUT` que promove a admin e um `PUT`/`DELETE` que trava os admins ativos podem se bloquear em ordem cruzada; em teoria, isso vira 1213 e 5xx, como no item acima. Não foi reproduzido; sem teste de concorrência, é hipótese.
- **Lote 3 (pré-condição: ator × alvo)**: antes de liberar grupos personalizados, decidir a regra geral de que o ator só age sobre alvo cujas permissões efetivas sejam subconjunto das suas. Hoje só o alvo admin é protegido; ficam abertos, por exemplo, conceder o grupo `personal_trainer` (troca de papel) por quem não tem essas permissões e resetar a senha de usuário de grupo com mais permissões que o ator.
- **Só autenticação não prova isolamento**: o pino de "só autenticação" não prova que o handler toca só dados do próprio usuário. As rotas de foto dependem da chamada a `authorize_self_or_all` (`api/app/access/service.py`, linha 233) no corpo; isso é coberto por teste de comportamento em `api/tests/unit/test_users_permissoes_rbac.py`, não pela guarda de cobertura.
- **Trava que conta só por grupo**: segue como aceita no 2A; o 2B não muda isso.
- **Fixtures de teste**: um teste que faz `TRUNCATE users` com `FOREIGN_KEY_CHECKS = 0` fora do `truncate_tables` do `conftest.py` deixa linhas órfãs em `user_access_groups`, e um id reaproveitado herda o grupo antigo. O `truncate_tables` já limpa `user_access_groups`.
- **Overrides mortos**: `test_users_fcmax_zonas.py` e `test_users_perfil_antropometrico.py` registram `dependency_overrides[require_admin]`, que não tem efeito nas rotas de `users` convertidas. Eles já trocam `resolve_permissions` por monkeypatch; o override pode sair numa limpeza.

### Ponto em aberto (lote 5)

- `trainer_students` não impede hoje que o treinador seja vinculado a si mesmo nem a um aluno inativo. A validação fica no serviço do lote 5, não na tabela nem na tela.

### Fora do escopo

- Cobrança e assinatura.
- Cota de IA por grupo.
- Convite com aceite do aluno.
- Exibir recurso bloqueado com convite para o plano pago.

## Trade-offs

**Ganhos**

- Um novo plano ou perfil vira um grupo com concessões, sem migração de ENUM nem deploy de código.
- A regra de acesso fica em um lugar só (o catálogo e o banco), e a guarda de cobertura impede rota sem permissão declarada.
- A virada é reversível: os guards antigos continuam existindo até o lote 2.

**Custos**

- Uma consulta a mais por requisição autenticada para carregar as permissões efetivas. Ela pode ser reduzida com cache por requisição, mas cache entre requisições exige invalidação na troca de grupo e não está decidido aqui.
- Duas fontes de verdade durante a transição: `users.role` e `user_access_groups`. Até o lote 2, a coluna `role` continua sendo lida por alguns caminhos. Qualquer mudança de papel precisa atualizar os dois, e a divergência é um risco até a coluna sair.
- O catálogo de recursos é código, então adicionar um recurso exige deploy. Isso é intencional: recurso novo precisa de guard e teste.
- Só concessões aditivas: para tirar acesso de alguém que está em dois grupos, é preciso sair dos dois. Isso é mais simples de auditar, mas menos flexível.
- Esconder no frontend depende de `/auth/me`. Se a chamada falhar, a interface fica sem menu até o próximo carregamento.

## Consequências

- No lote 1, nenhum guard ou rota muda de comportamento. A única mudança de contrato é o acréscimo aditivo de `permissions` e `groups` em `/auth/me`. O comportamento da virada é conferido comparando os guards antigos com os novos, no lote 2, antes de zerar a allowlist.
- As próximas decisões de acesso (cobrança, cota por grupo, visibilidade) entram como grupos e concessões, não como novos valores de `users.role`.
- Autores de migração, módulo de acesso e testes devem partir deste ADR. Qualquer desvio desta decisão exige um novo ADR que substitua este.
- O token mestre continua sendo admin, e o dispositivo continua herdando as permissões do dono. Isso precisa ser testado no lote 1, porque é o caminho que mais facilmente escapa de um guard novo.
- O último admin ativo não pode ser removido. Até o lote 1 esta regra não existia no código. Ela é entregue em dois momentos: no lote 2, como pré-condição (implementada no 2A, em produção desde 11/11/2026, e reforçada no 2B com a nova ordem de locks), a trava sobre `users` com as três entradas (`PUT /users/{id}` trocando o papel, `PUT /users/{id}` com `is_active = false` e `DELETE /users/{id}`, inclusive o admin agindo sobre si mesmo), contando só usuários ativos (`users.is_active = 1`) no grupo `admin` de sistema; no lote 3, a remoção de membro do grupo `admin`, no módulo `access`, junto com as mutações de grupo e membro e a auditoria. Não é aplicada só na tela.

## Impacto em produção

**Este lote altera produção.** Ele cria 5 tabelas, semeia dados e acrescenta campos a `GET /auth/me`. Nenhum guard, rota ou `users.role` muda de comportamento. Os lotes 2 a 4 precisam preservar o comportamento, conferido por teste de paridade (ver Pré-condições do lote 2). O único efeito de comportamento que a virada prevê por desenho é o do lote 5 (a PT passa a ver só os alunos vinculados), que será medido antes da promoção; os demais desvios possíveis são os das pré-condições do lote 2 e só são aceitos com paridade provada.

**O que muda (lote 1)**

- Migração 111 (`api/db/migrations/111_access_groups_rbac.py`) cria 5 tabelas: `access_groups`, `access_group_grants`, `user_access_groups`, `trainer_students` e `access_audit_log`.
- Semeia 4 grupos: `admin`, `personal_trainer` e `usuario` como grupos de sistema, e `free` vazio.
- Semeia 150 concessões: 71 do grupo `usuario` e 79 do grupo `personal_trainer`. O `admin` tem acesso total implícito, sem linhas.
- Associa cada usuário ao grupo do seu `users.role` atual.
- `GET /auth/me` passa a devolver os campos aditivos `permissions` e `groups`, com uma consulta a mais por chamada.
- Nenhum guard, rota ou `users.role` muda. No lote 1, nada lia as tabelas além de `/auth/me` (desde o 2A, a trava e a 112 também leem, e o `POST /users`, a troca de papel e o seed gravam `user_access_groups`; ver "Impacto em produção", Lote 2A). Não há FK para `exercise_catalog`.

**Efeito colateral conhecido: `scripts/prod_to_dev.py` (dois bloqueios)**

O script e o teste dele (`api/tests/unit/test_prod_to_dev.py`) não fazem parte do lote 1.

- **Bloqueio A, divergência de migração.** `main()` lê as migrações registradas em produção e chama `compare_migrations` contra os arquivos do repositório (`repo_migration_files()`) e, quando há destino local, contra as migrações do MySQL local (`scripts/prod_to_dev.py`, linhas 1035 a 1044). `compare_migrations` (linhas 771 a 784) levanta `AbortError` quando há migração só de um lado ("só em o repositório" ou "só em o MySQL local"). Logo, do merge deste PR na `develop` (o repositório e o DEV passam a ter a 111) até a promoção (produção passa a tê-la), a cópia de produção para o DEV aborta por divergência de migração. É o comportamento já existente do script para qualquer migração ainda não promovida; este lote não o agrava, mas a janela existe e o operador precisa saber. Decisão do Tech Lead: a janela é aceita; a cópia prod→DEV fica indisponível até a promoção do lote 1.
- **Bloqueio B, tabelas não classificadas.** `check_classification` (linhas 317 a 325) aborta com `AbortError` se houver em produção uma tabela não classificada no script, e as cinco tabelas da 111 não estão classificadas (listas em torno das linhas 78 a 199). Depois da promoção, a cópia (`scripts/prod_to_dev.py --user-id 1`) aborta até as cinco tabelas serem classificadas. Por isso o PR próprio de classificação precisa estar mesclado **antes** da promoção. Ele pode ser mesclado antes porque o script tolera tabela classificada que ainda não existe em produção (`present()`, linhas 560 a 561, usado nas linhas 563 a 597). Esse PR resolve só o bloqueio B, não o A. O teste dele deve usar o conjunto real de tabelas. Diretriz: `access_groups` e `access_group_grants` são configuração global; `user_access_groups` é dado ligado ao usuário copiado; `trainer_students` e `access_audit_log` envolvem terceiros e não são copiadas. A regra a garantir é que o usuário copiado para o DEV nunca fique sem grupo.

**Pré-requisitos da promoção** (uma única sequência)

1. O PR de classificação de `scripts/prod_to_dev.py` (bloqueio B) já está mesclado.
2. Ligar a variável de repositório `RUN_DB_MIGRATIONS=true` antes de disparar o deploy.
3. Disparar o deploy (`deploy-producao.yml`). A ordem dentro dele é: (a) atualizar o código da Lambda `musclefit-migrations`; (b) rodar o `migrate` só com `RUN_DB_MIGRATIONS == 'true'`; (c) atualizar as demais Lambdas (linhas 383 a 434). A 111 é aplicada, portanto, depois de a Lambda de migrações receber o código novo e antes de as demais receberem; não há janela de código novo sem migração. Não há `dry-run` prévio da 111: a Lambda de migrações roda a imagem publicada por último pelo backend e o `db-migrations.yml` não publica código (cabeçalho, linhas 15 a 16), então antes do deploy `status` e `dry-run` ainda enxergam a imagem antiga e não conhecem a 111. A prova em Aurora via Data API é o próprio `migrate`.
4. Depois do deploy:
   - `status` pelo `db-migrations.yml`, confirmando a 111 aplicada (ele mostra migrações, não contagens);
   - conferência por `SELECT` de leitura em produção: `access_groups` com 4 linhas (3 de sistema), `access_group_grants` com 150 (71 de `usuario`, 79 de `personal_trainer`) e uma linha em `user_access_groups` por usuário com papel conhecido (o seed usa `INSERT IGNORE`, que pode pular linhas em silêncio);
   - smoke de `GET /auth/me` para admin, personal trainer e usuário comum, com `permissions` e `groups` não nulos.
5. Voltar `RUN_DB_MIGRATIONS` a `false`.

**Caminho não adotado:** fazer o deploy com a variável em `false` para então rodar `dry-run` e `migrate` pelo `db-migrations.yml`. Isso deixaria o código novo no ar sem a 111 e abriria a janela degradada de `/auth/me` (permissões nulas e log de erro por chamada, ver a próxima seção).

**Ordem migração × código (lote 1)**

- O deploy de produção atualiza as Lambdas sempre, mas só roda a migração com `RUN_DB_MIGRATIONS=true`. Logo, com a variável desligada o código sobe sem a 111; a sequência dos pré-requisitos liga a variável justamente para evitar isso.
- Se isso acontecer, não há indisponibilidade: `/auth/me` responde 200 com `permissions: null` e `groups: null`, e registra um erro e um aviso de log por chamada. Isso não aciona alarme existente, porque a resposta é 200 e não há filtro de métrica de log em `infra/` (busca por `aws_cloudwatch_log_metric_filter` sem ocorrências); é ruído e custo de log, então a janela entre código e migração deve ser curta.
- Com a variável ligada e a migração falhando, o job de deploy para: as Lambdas da aplicação e o frontend ficam no código anterior, e a Lambda `musclefit-migrations` já está no código novo (`deploy-producao.yml`: o passo (a) vem antes do migrate, e o migrate antes do `update-function-code` das 7 Lambdas). Reexecutar o deploy converge.
- Contingência: se o `migrate` falhar por limite de tamanho da Data API no INSERT único de concessões (150 linhas, 600 parâmetros), a correção é uma migração nova que divida o seed em um INSERT por grupo. Cada INSERT é atômico, e o estado parcial só dá menos acesso, nunca mais.
- Motivo da escolha: neste lote ninguém decide nada por esses campos, e um 503 em `/auth/me` deixaria o app inteiro inacessível. `null` significa "permissões desconhecidas"; `{}` e `[]` significam "nenhuma".
- O backend continua sendo quem bloqueia: `require_permission` e `authorize_target` seguem respondendo 503 em falha de banco, nunca concedendo acesso.

**Estado parcial**

- Se as três tabelas lidas por `/auth/me` (`access_groups`, `access_group_grants`, `user_access_groups`) existirem e o seed tiver sido interrompido, `/auth/me` devolve `{}` e `[]` (não nulo) até a migração ser reexecutada. Se só parte dessas tabelas tiver sido criada, a consulta falha e a rota devolve `null`. Reexecutar converge: cada seed é um único statement e só entra com a tabela vazia.

**Rollback**

- Reverter só o código é seguro e não tem ordem obrigatória, porque o código antigo não lê as tabelas.
- O runner de migrações só executa `up`; o `down` não roda em produção.
- Remover o schema exige uma migração nova, e perderia o seed e, a partir do lote 3, edições de grupos, vínculos e auditoria.

**Limitação conhecida**

- A associação usuário→grupo é um retrato do momento da migração. Usuário criado ou com papel alterado depois dela depende da sincronização do lote 2 (ver Pré-condições do lote 2). Desde o 2A, a sincronização cobre `POST /users`, a troca de papel e o seed do admin, e a 112 reconcilia o retrato com `users.role` (ver "Decisões e pendências do 2A").

**Ainda depende de prova**

- O CI com o rótulo `ci-completo` (`ci-develop.yml`, job de integração com MySQL descartável; `ci-docker-images.yml`) prova a 111 em MySQL real pelo backend pymysql e constrói as três imagens. A execução pela RDS Data API contra o Aurora real (INSERT de concessões com 150 linhas e 600 parâmetros; `CASE` parametrizado contra coluna ENUM) só é provada no `migrate` que roda dentro do deploy da promoção (item 3 dos pré-requisitos), seguido de `status` e das conferências por `SELECT` (item 4). Não há `dry-run` prévio que prove a 111. Os testes unitários usam banco falso e não substituem essas execuções.

O que não foi medido: o resultado dos testes unitários do lote 1 (`api/tests/unit/test_access_*`, `test_auth_me_permissoes`, `test_migracao_111_access_groups`) não é evidência deste documento. A 111 não foi executada em MySQL real aqui.

**Lote 2A** (PR #124, promoção #125, em produção desde 11/11/2026; deploy 42937922299; branch `feat/rbac-lote2a-precondicoes`)

*O que muda:*
- Migração 112 (`api/db/migrations/112_user_access_groups_backfill.py`): só DML (DELETE de associações a grupo de sistema que não correspondem ao papel; INSERT IGNORE do grupo do papel). Sem DDL e sem FK. `down` não apaga nada. Pré-requisito: a 111. Sem as tabelas `access_groups` e `user_access_groups`, `plan` devolve plano vazio com log, sem erro e sem escrita (caso do `dry-run` com a 111 pendente). Com as tabelas presentes e algum grupo de sistema ausente, levanta `UserAccessGroupsBackfillError` antes de qualquer escrita.
- `POST /users`, a troca de papel em `PUT /users/{id}` e o seed do admin no boot passam a gravar `user_access_groups` na mesma transação de `users`.
- Novo 409 `last_admin` (corpo `{"error": "last_admin", "message": ...}`) em `PUT /users/{id}` (`is_active = false` ou troca de papel para um valor diferente de `admin`) e em `DELETE /users/{id}`.
- Sem a 111 aplicada: `DELETE /users/{id}` e `PUT /users/{id}` com `is_active = false` ou com troca de papel respondem 500, porque avaliam a trava ou leem `access_groups` pela troca de grupo. `POST /users` sempre lê `access_groups` (o papel é validado antes e sempre gera um grupo), então também responde 500. `PUT /users/{id}` sem troca de papel e sem desativação não consulta grupos.
- Associação: o INSERT em `user_access_groups` (`api/app/access/membership.py`, `_insert_membership`) é `INSERT ... ON DUPLICATE KEY UPDATE user_id = user_id`, e não `INSERT IGNORE`, para que erro de FK ou NOT NULL desfaça a transação do chamador. `assign_role_group` e `switch_role_group` rejeitam `user_id` que não seja inteiro positivo com `InvalidMembershipTargetError` (inclui `lastrowid` 0 ou None). As migrações 111 e 112 continuam com `INSERT IGNORE`.
- Custo de locks: toda desativação e todo rebaixamento travam todas as linhas de admin ativo durante a transação (ver "Trava do último admin", no checklist). O volume é baixo, porque as rotas são só de admin.
- `PUT /users/{id}` com `is_active = false` sobre a própria conta passa a responder 400 (antes aceitava).
- Se o grupo de sistema do papel não existir, `POST /users` e a troca de papel respondem 5xx e a transação desfaz tudo (`db_dataapi.transaction` e `db_pymysql.transaction` fazem rollback em exceção). No seed do admin não há 5xx: a exceção é registrada em log, o admin não é criado e a tentativa se repete no próximo boot.

*O que não muda:*
- Em produção, o comportamento sem a 111 não altera nada: a 111 já está aplicada desde 10/11/2026, então a 112 planeja as mesmas linhas de antes.
- Nenhum guard. Nenhuma rota declara `require_permission` (o símbolo existe em `api/app/access/deps.py`, sem uso em `api/app`). `users.role` segue gravado e lido.
- Nenhuma rota nova e nenhuma resposta de sucesso muda de formato.

*Promoção:*
- Mesma sequência do lote 1 (`RUN_DB_MIGRATIONS=true` antes do deploy, `RUN_DB_MIGRATIONS` de volta a `false` depois), com a 112 aplicada pelo `migrate`, antes de as Lambdas da aplicação receberem o código novo. Num banco sem a 111, a 112 não faz nada (plano vazio); quem garante a 111 antes é a ordem do runner, que planeja e aplica uma migração por vez no `migrate`. Um `dry-run` com a 111 pendente mostra plano vazio para a 112 e não prevê as inserções de grupo; isso é esperado, não é falha.
- Depois do deploy, `SELECT` de leitura conferindo: nenhum usuário com papel conhecido (`admin`, `personal_trainer`, `regular`) sem linha no grupo de sistema do papel; nenhuma linha de grupo de sistema divergente do papel do usuário. Usuário com papel fora dos três valores deve ficar sem grupo de sistema, e isso é o esperado.

*O que foi medido:*
- Testes unitários com banco falso (`api/tests/unit/test_access_sincronizacao_grupo.py`, `test_users_trava_ultimo_admin.py` e `test_migracao_112_backfill_grupos.py`). Eles não substituem as provas abaixo.
- A 112 no Aurora, pela RDS Data API, na promoção #125: aplicada pelo `migrate` com 0 statements. Conferência por `SELECT` em 11/11/2026: 4 usuários, todos com o grupo de sistema do papel, nenhum divergente. `RUN_DB_MIGRATIONS` voltou a `false`.

*O que não foi medido:*
- A 112 em MySQL real pelo CI com o rótulo `ci-completo`: não há execução registrada neste documento. A execução no Aurora já ocorreu na promoção (item acima).
- Rotas de usuários por HTTP com sessão real em produção: não exercitadas no 2A.
- Concorrência real da trava e dos locks, inclusive o deadlock entre admins: não há teste com transações concorrentes. A ordem (linha do alvo, depois admins ativos, depois o `UPDATE`) é verificada só em banco falso, pelos statements emitidos.
- O número atual de usuários divergentes em produção. A conferência de 10/11 achou 4 usuários, todos com grupo; repetida em 11/11, achou 4 usuários e nenhum divergente.

**Lote 2B** (branch `feat/rbac-lote2b-base-da-troca`, não publicado, sem PR, não promovido)

*O que muda:*
- As 9 rotas com `require_permission` passam a decidir por grupo: as cinco de `users` (`GET /users`, `POST /users`, `PUT /users/{id}`, `DELETE /users/{id}` e `POST /users/{id}/reset-password`) e as quatro de `devices`. Usuário sem o grupo de sistema do papel recebe 403 nelas; antes a decisão era de `users.role`.
- Parear, listar e revogar relógio e `GET /devices/me` passam a depender de `conta.dispositivos`, que só existe no banco depois da 113.
- Fotos com id de alvo menor ou igual a 0 respondem 400 `invalid_target`.
- Quem tem só `admin.usuarios` não cria admin nem faz nenhuma escrita sobre um admin (editar, reativar, desativar, excluir, resetar senha, promover, rebaixar): 403. `DELETE /users/{id}` passou a ler também o papel do alvo. As rotas de `/users` sobre alvo que não está entre os admins travados fazem uma consulta a mais de pertença ao grupo `admin`.
- As rotas convertidas fazem uma consulta de permissões por requisição. Não consultam o token mestre (acesso total sem banco) nem a foto do próprio usuário. Se a consulta falhar, a resposta é 503.
- A ordem de locks da desativação e do rebaixamento mudou (admins antes do alvo).
- A migração 113 é só DML.

*O que não muda:*
- Respostas de sucesso e o formato delas.
- `users.role` continua sendo gravado.
- Token mestre segue com acesso total, sem banco. Dispositivo herda as permissões do dono.
- Nenhum guard antigo foi removido: `api/app/security.py` não mudou. As demais rotas da dívida (343) seguem com o guard antigo.
- A 111 e a 112 não foram editadas.

*Promoção* (mesma sequência do lote 1 e do 2A):
1. Antes do deploy, repetir a conferência por `SELECT` de leitura: nenhum usuário ativo sem o grupo de sistema do papel e nenhuma linha de grupo de sistema divergente do papel. A última conferência, de 11/11, achou 4 usuários e nenhum divergente. Conferir também, por `SELECT` de leitura, que todo dono de dispositivo ativo está num grupo com `conta.dispositivos`/`ver` (na prática, no grupo de sistema do papel).
2. Ligar `RUN_DB_MIGRATIONS=true` antes do deploy. O `migrate` aplica a 113 antes de as Lambdas da aplicação receberem o código novo (ordem de `deploy-producao.yml`, como no 2A).
3. Depois do deploy: `SELECT` das 6 linhas de `conta.dispositivos` (3 em `usuario` e 3 em `personal_trainer`); `/health`; `GET /users` com sessão real de admin; `GET /devices` com sessão real de usuário comum; e um `GET /devices/me` com token de dispositivo real (o caminho do relógio; o smoke com sessão não o cobre).
4. Voltar `RUN_DB_MIGRATIONS` a `false`.

*Nota (conferida nesta curadoria em `.github/workflows/deploy-producao.yml`, por leitura, não por execução):* o passo `migrate` (linhas 425 a 427, com `if: vars.RUN_DB_MIGRATIONS == 'true'`) não tem `continue-on-error`, e o `update-function-code` das 6 Lambdas da aplicação (linha 434) não tem `if:`. Assim, uma falha do `migrate` faz o job parar antes da atualização das Lambdas.

*Janela de risco:* se o código subir sem a 113, usuário comum e personal trainer recebem 403 em `GET /devices` e `GET /devices/me`, e o relógio para de sincronizar o que depende de `GET /devices/me`. A ordem do deploy evita isso; reexecutar o deploy converge.

*Rollback:* reverter só o código é seguro, porque o código anterior ignora as linhas da 113. O `down` da 113 não roda em produção.

*Medido (relato dos autores, não reexecutado nesta curadoria):* 19 arquivos de teste unitário do manifesto do lote, 756 passed depois da correção da rodada 1 (antes 711); varredura de unitários por palavra-chave, 1695 passed; coleta dos testes de integração sem erro, 1795 coletados. Nenhum teste de integração foi executado.

*Não medido:*
- A 113 em MySQL real (só no CI, com o rótulo `ci-completo`) e pela RDS Data API no Aurora (só no `migrate` da promoção).
- Os testes de integração ajustados (só o CI roda).
- Concorrência real dos locks, inclusive o deadlock entre admins.
- As rotas por HTTP com sessão real em produção.
- O número atual de usuários divergentes em produção (última conferência, de 11/11: 4 usuários, nenhum divergente).
- O `INSERT IGNORE` da 113 (`api/db/migrations/113_access_grants_conta_dispositivos.py`, linha 77) pode engolir erro que não seja duplicidade; a conferência das 6 linhas por `SELECT` depois do deploy é o que detecta isso.

**Lote 2C-1** (branch `feat/rbac-lote2c1-admin-declarado`, não publicado, não promovido)

*O que muda:*
- As 34 rotas passam a decidir por grupo. Usuário sem o grupo de sistema do papel recebe 403 nelas, inclusive em `GET /exercises/{id}/card`, `GET /next-set-load-rules/published` e `GET /mapping-canonical/options`, que antes aceitavam qualquer autenticado.
- As rotas convertidas fazem uma consulta de permissões por requisição. Se a consulta falhar, a resposta é 503.
- O corpo do 403 passa a ser o de `FORBIDDEN_DETAIL`.
- `/training-types` depende de `modelos.tipos_treino`, que só existe no banco depois da 114.

*O que não muda:*
- Respostas de sucesso.
- Limite diário e flag de geração de cards.
- Token mestre segue com acesso total, sem banco. Dispositivo herda as permissões do dono.
- `api/app/security.py` não mudou.
- As 309 rotas em dívida seguem com o guard antigo.
- A 111, a 112 e a 113 não foram editadas.
- Nenhuma regra de motor.

*Promoção* (mesma sequência do 2B):
1. SELECT prévio de leitura: nenhum usuário ativo sem o grupo de sistema do papel e nenhuma linha de grupo de sistema divergente do papel. Este passo é obrigatório antes da promoção e ainda NÃO foi executado para o 2C-1. A última medição é a de 11/11/2026, do 2B: 4 usuários, nenhum divergente.
2. Ligar `RUN_DB_MIGRATIONS=true` antes do deploy. O `migrate` aplica a 114 antes de as Lambdas da aplicação receberem o código novo.
3. Depois do deploy: SELECT das 3 linhas de `modelos.tipos_treino` no grupo `personal_trainer`; `/health`.
4. Voltar `RUN_DB_MIGRATIONS` a `false`.

O PR toca migração e por isso leva o rótulo `ci-completo` no último push, logo antes do merge.

*Janela de risco:* se o código subir sem a 114, personal trainer recebe 403 em `/training-types`. O `migrate` só roda com `RUN_DB_MIGRATIONS=true`; com a variável diferente de `true`, o workflow só emite um aviso (`.github/workflows/deploy-producao.yml`, linhas 430 a 432), o código sobe sem a 114 e o problema acontece. O SELECT das 3 linhas depois do deploy é o que detecta. A correção é ligar a variável e reexecutar o deploy.

*Rollback:* reverter só o código é seguro. O `down` da 114 não roda em produção.

*Medido:* unitários do lote reexecutados pelo Tech Lead depois da correção da falha em `test_access_catalogo_e_resolucao.py::test_os_guards_de_papel_continuam_nos_mesmos_modulos` (a lista esperada deixou de incluir `training_types.py`), em 12 arquivos: `test_access_guarda_cobertura_rotas.py`, `test_access_paridade_guards.py`, `test_access_retrato_guards.py`, `test_access_guarda_identity_role.py`, `test_access_catalogo_e_resolucao.py`, `test_exercise_cards_routes.py`, `test_next_set_load_rules_admin.py`, `test_seg03_catalogos_autorizacao.py`, `test_catalogos_admin_permissoes_rbac.py`, `test_migracao_114_concessoes.py`, `test_migracao_113_concessoes.py` e `test_migracao_111_access_groups.py`: 547 passed, 0 failed. Relato dos autores, anterior à correção e não reexecutado nesta curadoria: varredura de unitários por palavra-chave, 1714 passed. Coleta dos 4 arquivos de integração ajustados: 112 coletados, sem erro.

*Não medido:*
- A 114 em MySQL real (só no CI, com `ci-completo`) e pela RDS Data API no Aurora (só no `migrate` da promoção).
- Os 4 testes de integração ajustados (só o CI roda): `test_m12_vigencia_metas_ativacao.py`, `dataapi/test_exercise_cards_fluxo.py`, `dataapi/test_exercise_card_references_sql.py` e `dataapi/test_migration_032_033_seguranca_equipamento.py`.
- Rotas por HTTP com sessão real em produção.
- A releitura ao fim do `up` da 114 depende de a transação do runner enxergar as próprias escritas. Isso vale para o runner (`api/app/migrate.py`, commit depois de `up`). O comportamento pela RDS Data API não foi provado por leitura e só se confirma no `migrate` da promoção. Se a releitura falhar ali, o job de deploy para antes de atualizar as Lambdas da aplicação.
- Não foi provado por leitura que o API Gateway roteia `PUT /analytics/muscle-group-body-map` para a Lambda engines.

## Referências

- `api/app/security.py`: `get_current_identity`, `require_admin`, `require_admin_or_pt`, `Identity`, `master_token_matches`.
- `api/app/nutrition_access.py`: `resolve_target_user` (a ser substituído por `authorize_target`).
- `api/app/auth.py`: `me`; `ensure_admin_seed` (lote 2A: grava o grupo `admin` do seed).
- `api/app/users.py`: `_ALLOWED_ROLES`; `create_user`, `update_user` e `deactivate_user` (lote 2A: sincronização e trava).
- `api/app/access/membership.py`: escrita de `user_access_groups` e trava do último admin (lote 2A).
- `api/db/migrations/112_user_access_groups_backfill.py`: reconciliação dos grupos de sistema com `users.role` (lote 2A).
- `scripts/smoke-async-jobs.sh` (bloco `smoke-admin`) e `scripts/prod_to_dev.py` (`verify_snapshot`).
- `api/db/migrations/001_schema.sql` e `007_workout_plan_templates.sql`: origem do ENUM de `users.role`.
- `api/app/domain/nutrition/ai_usage.py`: dono da cota de IA de nutrição.
- `frontend/src/lib/nav-items.ts`: menu central.
- Lote 2B: `api/app/access/catalog.py` (`conta.dispositivos`); `api/app/access/service.py` (`validate_target_user_id`, `authorize_target`, `authorize_self_or_all`); `api/app/access/deps.py` (`require_additional_permission`, `permissions_for_request`); `api/app/access/membership.py` (`ensure_not_last_admin`, `locked_admin_ids`); `api/app/users.py` (`create_user`, `update_user`, `deactivate_user`, fotos); `api/app/devices.py` (rotas de relógio); `api/app/jobs.py`; `api/app/auth.py` (`me`, `change-password`); `api/db/migrations/113_access_grants_conta_dispositivos.py`; `api/tests/support/access_retrato.py` e `api/tests/unit/data/access_retrato_guards_antigos.json`; `api/tests/unit/test_access_retrato_guards.py`, `test_access_paridade_guards.py`, `test_access_guarda_identity_role.py` (com `access_identity_role_allowlist.json`), `test_access_guarda_cobertura_rotas.py` (com `access_cobertura_allowlist.json`), `test_users_permissoes_rbac.py`, `test_devices_permissoes_rbac.py`, `test_users_trava_ultimo_admin.py` e `test_migracao_113_concessoes.py`; `api/tests/integration/conftest.py` (`conceder_grupo_do_papel`, fixture `conceder_grupo`, fixture `catalog`).
- Lote 2C-1: `api/app/access/catalog.py` (`modelos.tipos_treino`); `api/db/migrations/114_access_grants_modelos_tipos_treino.py`; `api/app/exercise_cards.py`, `api/app/training_types.py`, `api/app/next_set_load_rules_admin.py`, `api/app/training_safety_admin.py`, `api/app/exercise_mapping_canonical.py`, `api/app/main.py` (`POST /exercise-mappings`) e `api/app/dashboard_modules.py` (`PUT /analytics/muscle-group-body-map`); `api/tests/unit/test_migracao_114_concessoes.py` e `api/tests/unit/test_catalogos_admin_permissoes_rbac.py`.
- ADR relacionado de forma indireta: [0010](0010-regras-de-carga-como-formula-com-condicionais.md) (formato de decisão com "Impacto em produção").

# ADR 0032: Permissão própria para acrescentar equipamento à academia (issue #246)

- **Status**: Aceita. Branch `feat/permissao-acrescimo-l26`. Não publicada nem promovida a produção.
- **Data**: 2026-11-22
- **Relaciona-se a**: [ADR 0011](0011-permissoes-por-grupos-rbac.md) (RBAC: recurso × ação × escopo) e [ADR 0028](0028-catalogo-global-no-seletor-de-equipamento.md) (acréscimo pelo seletor; as decisões 2 e 3 e a próxima decisão sobre ação própria ficam resolvidas aqui). Nenhuma decisão anterior é substituída por reescrita; a 0028 recebe nota de atualização.

## Contexto

- `POST /gym-equipment-catalog` (`api/app/gyms.py::create_gym_equipment_catalog`) exigia `treino.academias`/`editar` e a posse da academia (`_assert_gym_equipment_catalog_writable`: dono ou escopo `todos`). Com isso, quem recebia `treino.academias`/`editar` também editava e excluía vínculos de todas as academias, se tivesse `todos`.
- No seletor de equipamento do Modo Treino, o acréscimo só aparecia com `can('treino.academias', 'editar', 'todos')` (`frontend/src/app/(fullscreen)/modo-treino/EquipmentSubstituteModal.tsx`, ADR 0028 decisão 3). O dono da academia podia acrescentar pela API e pela tela de Academias, mas não pelo seletor.
- A tela de Academias usa `academiasControls` (`frontend/src/lib/screen-permissions.ts`), que hoje lia `treino.academias`/`editar` para o acréscimo.
- Escolha do usuário em 22/11/2026 (#246): (1) o dono da academia vê "acrescentar equipamento do catálogo" no seletor do Modo Treino, na academia dele; (2) a permissão de acrescentar é própria, separada de editar e excluir a academia ou os vínculos.

## Alternativas consideradas

- **Ação nova `criar` no vocabulário de ações.** Recusada. As ações são fixas (`ACTIONS` em `api/app/access/catalog.py`), e uma ação nova mudaria todas as matrizes, as paridades de teste e as concessões existentes. A ADR 0028 já a tinha recusado pelo mesmo motivo.
- **Manter `treino.academias`/`editar`.** Recusada pelo usuário: quem pode acrescentar também edita e exclui vínculos, e com escopo `todos` isso vale para todas as academias.
- **A tela comparar `owner_user_id` com o usuário logado.** Recusada: reimplementa a posse no frontend. O padrão do projeto é o servidor entregar o campo pronto, como `can_edit_manufacturer`.
- **Semear a permissão só nos grupos de sistema.** Recusada: um grupo personalizado com `treino.academias`/`editar` perderia o acréscimo sem aviso.

## Decisão

1. **Recurso novo `treino.academias.equipamentos`**, rótulo "Acrescentar equipamento à academia", domínio `Treino`, em `api/app/access/catalog.py` (`Resource`). A matriz o inclui em `_DADOS_PROPRIOS`, então `usuario` e `personal_trainer` recebem as três ações no escopo `proprios`. Só `editar` tem efeito; `ver` e `excluir` existem por coerência com a matriz de três colunas da tela de grupos. `admin` tem acesso total por código (`FULL_ACCESS_GROUP`).
2. **Migração 122** (`api/db/migrations/122_access_grants_academias_equipamentos.py`): só DML em `access_group_grants`. Para cada grupo, de sistema ou personalizado, que tem concessão em `treino.academias`, copia a mesma ação com o mesmo escopo para o recurso novo, com `INSERT IGNORE`. Não sobrescreve concessão existente. Plano vazio se as tabelas da 111 não existem. `down` remove só as linhas do recurso novo. Em produção o runner não executa `down`.
3. **Rotas.** `POST /gym-equipment-catalog` passa a exigir `treino.academias.equipamentos`/`editar` (`require_permission`), e a posse é conferida com o mesmo recurso (`_assert_gym_equipment_catalog_writable(..., resource=_RESOURCE_GYM_ADD_EQUIPMENT)`). `PATCH` e `DELETE /gym-equipment-catalog/{id}` seguem em `treino.academias` (`editar` e `excluir`). `POST`, `PUT` e `DELETE /gym-equipment` seguem em `treino.academias`.
4. **Campo aditivo de topo** `can_add_to_gym` em `GET /gym-equipment` (`gyms.py::list_gym_equipment`). Vem só quando há `gym_id`; o valor sai de `can_write_gym_catalog` (existe, e dono ou `treino.academias.equipamentos`/`editar` com `todos`). Academia inexistente resulta em `false`.
5. **`POST /gym-equipment` com `mapping_id` inexistente** responde 404, depois da checagem de posse (`gyms.py::create_gym_equipment`). Quem não tem posse continua recebendo 403 antes de qualquer consulta.
6. **Frontend.** O seletor habilita o acréscimo por `can_add_to_gym` (`EquipmentSubstituteModal.tsx`, `canAddToGym`), e não mais por `can('treino.academias', 'editar', 'todos')`. A tela de Academias usa `academiasControls.canAddEquipment` (`screen-permissions.ts`), que lê `treino.academias.equipamentos`/`editar`, só para marcar equipamento ainda não vinculado. Nenhuma regra de posse nasce no frontend.

## Trade-offs

- Ganha: o dono acrescenta equipamento no treino sem receber edição ou exclusão de vínculos, e quem tem só o recurso novo não consegue editar ou excluir.
- Ganha: a posse continua sendo decidida no servidor; o frontend só lê o campo.
- Paga: um recurso a mais no catálogo, com duas ações sem efeito (`ver` e `excluir`) na matriz.
- Paga: uma consulta a mais em `GET /gym-equipment` quando há `gym_id`.
- Paga: duas fontes de permissão para a mesma academia (o recurso novo para acrescentar, `treino.academias` para o resto). Quem migra concessões manualmente precisa lembrar das duas.

## Consequências

- **Dados.** A migração 122 insere linhas em `access_group_grants`; nenhuma coluna nova, nenhuma tabela nova. Reexecutar converge.
- **Contrato.** `POST /gym-equipment-catalog` passa a exigir o recurso novo. `GET /gym-equipment` ganha `can_add_to_gym` (aditivo). `POST /gym-equipment` ganha o 404 de `mapping_id`.
- **Operação.** A promoção exige `RUN_DB_MIGRATIONS=true` para aplicar a 122 antes do código novo.
- **Próximas decisões.** Incluir equipamentos sem vínculo a exercício do grupo na seção do seletor (desvio 1 da ADR 0028) segue em aberto. Migrar `POST /gym-equipment` e `PUT /gym-equipment/{id}` para outro recurso, se o usuário quiser separar o vínculo por exercício, segue em aberto.

## Impacto em produção

Medição do Tech Lead em 22/11/2026, só leitura em produção:

- Grupos: `admin` (1 usuário; acesso por código, sem concessões); `usuario` (3 usuários; 74 concessões; `treino.academias`/`editar` = `proprios`); `personal_trainer` (0 usuários; 85 concessões; `treino.academias`/`editar` = `proprios`); um grupo personalizado `free` (0 concessões, 0 usuários).
- A migração 122 insere as três ações do recurso novo em `proprios` para `usuario` e `personal_trainer`: 6 linhas.
- No deploy ninguém ganha nem perde acesso pela API. O dono já podia acrescentar pelo servidor. A mudança visível é a tela passar a mostrar a ação ao dono no seletor do Modo Treino.

**Não medido:** nada em tela no navegador; a migração 122 não foi executada em banco (roda pela pipeline com `RUN_DB_MIGRATIONS=true`).

## Limites

- Só `editar` do recurso novo tem efeito.
- O acréscimo reativa vínculo inativo (o `POST` faz `ON DUPLICATE KEY UPDATE is_active = 1`), então "acrescentar" inclui "reativar".
- `POST /gym-equipment` (vínculo por exercício) e `PUT /gym-equipment/{id}` continuam em `treino.academias`/`editar`.
- Quem tiver só `treino.academias`/`editar` e não tiver o recurso novo deixa de acrescentar pela API. No deploy isso não ocorre para nenhum grupo existente, porque a 122 copia o escopo.
- Grupo personalizado criado depois da 122 não recebe o recurso novo automaticamente; o admin concede pela tela de Grupos e acessos.
- As rotas que recebem o vínculo por `link_id` (`PUT` e `DELETE /gym-equipment/{link_id}`, `PATCH` e `DELETE /gym-equipment-catalog/{link_id}`) seguem distinguindo 404 de 403, como na ADR 0028 (limite f).

## Verificação

Arquivos de teste alterados ou criados na branch (este ADR não registra resultado de execução):

- `api/tests/unit/test_migracao_122_concessoes.py` (novo).
- `api/tests/unit/test_access_catalogo_e_resolucao.py`, `api/tests/unit/test_access_paridade_guards.py`, `api/tests/unit/test_gyms_permissoes_rbac.py`, `api/tests/unit/test_gym_equipment_convencao_de_carga.py` e `api/tests/unit/test_seg03_catalogos_autorizacao.py`.
- `frontend/src/lib/screen-permissions.test.ts`, e os e2e `frontend/e2e/workout-resilience/mock-workout-api.ts` e `frontend/e2e/mobile-layout/access-grants.generated.json`.
- Não verificado neste ADR: a execução dos testes acima; SQL da 122 contra MySQL real (roda só no CI, com o rótulo `ci-completo`, porque a entrega tem SQL novo) e pela RDS Data API no `migrate` da promoção; tela no navegador ou no iPhone.

# ADR 0028: Catálogo global no seletor de equipamento e acréscimo à academia com permissão (issue #163, segunda parte)

- **Status**: Aceita. Branch `feat/catalogo-global-163b`. Não publicada nem promovida a produção.
- **Data**: 2026-11-20 (verificação complementar em 2026-11-21; notas de resolução em 2026-11-22, ver ADR 0032)
- **Complementa**: ADR 0027 (seletor de equipamento, primeira parte). Nenhuma decisão anterior é substituída.

## Contexto

A primeira parte da #163 (ADR 0027) deixou o seletor com os equipamentos da academia do treino. A segunda parte pede o catálogo global e a permissão para acrescentar à academia um equipamento que ela não tem. A decisão do usuário, de 20/11/2026, é literal:

> "Mostra o catálogo global inteiro, permitindo adicionar à academia mas somente para usuários de grupos com permissão. A princípio apenas o grupo admin terá essa permissão."

Restrições reais:

- o acréscimo grava em `gym_equipment_catalog` (`api/app/gyms.py::create_gym_equipment_catalog`) e depois sincroniza o cache `gym_equipment` (`sync_gym_equipment_for_gym`). Um equipamento sem vínculo em `equipment_exercises` não gera associação `gym_equipment` e, mesmo acrescentado, não pode ser escolhido para exercício nenhum;
- a autorização das rotas de `gym_equipment_catalog` é `treino.academias` (`editar` para POST e PATCH, `excluir` para DELETE), com a posse da linha: dono da academia ou escopo `todos` (`api/app/gyms.py::_assert_gym_equipment_catalog_writable`);
- as ações do RBAC são fixas (`ver`, `editar`, `excluir`; `api/app/access/catalog.py`). O grupo `admin` tem acesso total por código (`api/app/access/service.py`), e a concessão de `treino.academias` com escopo `todos` pode ser dada a outros grupos (ADR 0011);

## Alternativas consideradas

- **Mostrar o catálogo global inteiro, sem filtro.** Recusada como está: equipamentos sem nenhum vínculo a exercício não podem ser escolhidos para o exercício depois do acréscimo. Ver o desvio abaixo.
- **Criar uma ação nova de RBAC (por exemplo `acrescentar`) só para esta operação.** Recusada: as ações são fixas para todos os recursos. Uma ação nova mudaria `ACTIONS` e exigiria migração das concessões existentes, sem ser necessária para o pedido.
- **Gate fixo no código para o grupo admin.** Recusada: a decisão do usuário diz "a princípio", e o RBAC já permite conceder a outros grupos sem código novo.
- **Permitir o acréscimo pela fila offline.** Recusada: o acréscimo grava dado compartilhado da academia e é uma ação de escrita pontual; sem rede, o item mostra o aviso e fica bloqueado (decisão 5).

## Decisão

1. **Seção "Outros equipamentos do catálogo".** O seletor ganha a terceira seção, montada por `frontend/src/lib/equipment-catalog-add.ts::buildCatalogSection` (título em `frontend/src/lib/equipment-picker.ts`, `SECTION_TITLES.catalog`). Ela recebe os pares equipamento × exercício do grupo muscular de `GET /equipment-exercises?primary_group=...&active_only=true` e mostra os que a academia do treino não tem. A ordem é a da API, com os pares do exercício atual primeiro. O casamento com a academia é em `isSamePair`: por `equipment_id` e, quando o item traz `exercise_id` (ver limite (a)), pelo id canônico; sem o campo, por `mapping_id` ou pelo nome normalizado do exercício.

   **Desvio declarado em relação à decisão.** A decisão diz "o catálogo global inteiro". A seção mostra só equipamentos ligados a algum exercício do grupo em `equipment_exercises`. O motivo é o acima: um equipamento sem vínculo não gera `gym_equipment` e não seria escolhível para exercício nenhum depois de acrescentado. Equipamentos sem vínculo a exercício do grupo ficam fora da seção.

2. **Permissão reutiliza `treino.academias` / `editar` com escopo `todos`.** Nenhuma ação nova, nenhuma permissão nova, nenhuma migração. A rota de acréscimo continua sendo `POST /gym-equipment-catalog`, a mesma usada pela tela de Academias. **Consequência declarada:** conceder `treino.academias` com escopo `todos` a um grupo também dá edição e exclusão de todas as academias, porque as ações são fixas. Não há granularidade menor sem uma ação nova, e a ação nova fica como decisão futura se o usuário quiser separar.

3. **Dono da academia continua podendo acrescentar.** A regra do servidor não muda: dono ou escopo `todos` (`_assert_gym_equipment_catalog_writable`). No seletor, porém, o botão só aparece com o escopo `todos` (`EquipmentSubstituteModal.tsx`, `usePermissions().can('treino.academias', 'editar', 'todos')`). **Diferença conhecida em relação a "só admin":** o dono da academia pode acrescentar pela tela de Academias e pela API, mas não pelo seletor do Modo Treino, a não ser que tenha o escopo `todos`. **Resolvido em 22/11/2026 (ADR 0032):** o seletor usa o campo `can_add_to_gym` do servidor, e o dono vê a ação na academia dele sem o escopo `todos`.

4. **Sem permissão, o item aparece desabilitado.** O texto é "Não cadastrado nesta academia" (`CATALOG_NOT_IN_GYM_NOTICE`, `catalogItemState`).

5. **O acréscimo exige rede.** Offline, o item mostra "Acrescentar à academia exige conexão" (`CATALOG_NEEDS_ONLINE_NOTICE`) e não entra na fila offline. A confirmação pergunta antes de gravar (`formatCatalogAddQuestion`).

6. **Servidor endurecido sem mudar a autorização.**
   - corpo tipado: `GymEquipmentCatalogAddIn` (`api/app/request_models.py`) com `gym_id` e `equipment_id` como inteiros estritos e positivos, e campos extras recusados (`_StrictIn`, `extra="forbid"`). Corpo inválido responde 422; antes respondia 400;
   - equipamento inativo é recusado com 409 (`create_gym_equipment_catalog`). Antes era aceito;
   - a posse é checada antes da consulta ao equipamento (`create_gym_equipment_catalog`). Quem não é autorizado recebe o mesmo 403 para equipamento ativo, inativo ou inexistente, e não descobre se o id existe. Nada é gravado nesse caso. A posse também vem antes da existência da academia (`_assert_gym_equipment_catalog_writable`);
   - a resposta ganha `links` (ids das associações ativas do equipamento na academia, lidos depois do sync). O campo é aditivo.

   Tabela de respostas: 403 para quem não é dono nem tem `todos` (academia alheia, academia inexistente ou equipamento em qualquer estado); com `todos`, 404 para academia ou equipamento inexistente e 409 para equipamento inativo.

7. **Sem migração, sem permissão nova, sem rota nova.**

## Trade-offs

- Ganha: a academia cresce sem sair do treino, e quem tem o escopo `todos` não precisa ir à tela de Academias.
- Ganha: o servidor deixa de aceitar equipamento inativo e de revelar a existência de ids para quem não pode escrever.
- Paga: o escopo `todos` de `treino.academias` dá edição e exclusão de todas as academias a quem o recebe. A granularidade menor depende de uma ação nova. Resolvido em 22/11/2026 (ADR 0032): a permissão própria é o recurso `treino.academias.equipamentos`, sem ação nova.
- Paga: o dono da academia perde o atalho no seletor. A permissão de acrescentar pela tela de Academias segue como estava. Resolvido em 22/11/2026 (ADR 0032): a permissão de acrescentar passou a ser própria, e o dono vê a ação no seletor.
- Paga: a seção mostra só pares com vínculo no grupo, o que é mais estreito que a decisão.
- Paga: o acréscimo depende de rede, e o item fica indisponível offline.

## Consequências

- **Dados.** Nenhuma coluna nova, nenhuma migração, nenhum registro existente muda por esta entrega.
- **Contrato.** `POST /gym-equipment-catalog` responde 422 para corpo inválido (antes 400) e 409 para equipamento inativo (novo). A resposta de sucesso ganha `links`. `GET /gym-equipment` ganha `exercise_id` (campo aditivo). `GET /equipment-exercises` aceita `limit` e `offset` opcionais (lote 32). As escritas de vínculo (`/gym-equipment`, `/gym-equipment-catalog`) respondem 403 (antes 404) para academia inexistente a quem não tem escopo `todos`.
- **Medições (20/11/2026).** Lidas em produção, só SELECT: 77 equipamentos ativos e 1 inativo no catálogo; 145 vínculos ativos em `equipment_exercises`, de 74 equipamentos distintos; 7 academias, todas com dono (um único usuário), com 65, 19, 11, 1, 9, 26 e 40 itens ativos de catálogo.
- **Impacto para clientes existentes.** Corpo incompleto passa de 400 para 422. Equipamento inativo passa a ser recusado. A tela de Academias envia o mesmo corpo e continua aceita.
- **Não medido.** Volume de resposta de `GET /equipment-exercises?primary_group` por grupo (a rota ganhou paginação opcional no lote 32, mas sem `limit` ela segue devolvendo tudo; o teto hoje é 145 pares no total). Quantas chamadas com equipamento inativo ocorreram, pois o servidor não registrava. Quantas chamadas de escrita de vínculo com academia inexistente ocorreram.
- **Mapa.** Nenhuma regra de motor foi criada, e `docs/motores/FONTES_DA_VERDADE.md` não muda nesta entrega.
- **Próximas decisões.** Resolvido em 22/11/2026 (ADR 0032): a permissão própria de acrescentar equipamento é o recurso `treino.academias.equipamentos`. Incluir equipamentos sem vínculo a exercício na seção, se o usuário quiser o catálogo inteiro (desvio 1).

## Limites e pendências

- (a) Casamento catálogo × academia: `GET /gym-equipment` devolve `exercise_id` (campo aditivo, `COALESCE(elm.canonical_exercise_id, ge.mapping_id)`, a mesma expressão canônica do join da convenção de carga, em `list_gym_equipment`). `isSamePair` casa por esse id quando o campo vem. A reserva por nome normalizado do exercício só vale para resposta antiga sem o campo, e só quando o `mapping_id` também não casa (`frontend/src/lib/equipment-catalog-add.ts`). Se o `mapping_id` do treino for id legado, o item do exercício atual pode cair no bloco dos demais.
- (b) RESOLVIDO em 23/11/2026 (lote 34): a perda de rede na recarga agora mostra mensagem própria (`CATALOG_ADDED_REFRESH_FAILED_MESSAGE`, ver a atualização abaixo). Antes, a perda de rede entre o acréscimo e a recarga da lista mostrava "O equipamento foi acrescentado à academia, mas não está ligado a este exercício no catálogo" (`CATALOG_ADDED_WITHOUT_LINK_MESSAGE`), embora o acréscimo tenha ocorrido.
- (c) Sem teste de renderização do modal. A cobertura é o e2e de resiliência do Modo Treino.
- (d) O nome da academia vem dos itens de `/gym-equipment`. Academia sem nenhum item no grupo mostra "esta academia" na pergunta de confirmação.
- (e) Resolvido em 22/11/2026 (ADR 0032): o dono vê a ação no seletor pelo campo `can_add_to_gym`, sem o escopo `todos` (decisão 3).
- (f) As rotas que recebem o vínculo por `link_id` (`PUT`/`DELETE /gym-equipment/{link_id}`, `PATCH`/`DELETE /gym-equipment-catalog/{link_id}`) ainda distinguem vínculo inexistente (404) de vínculo de outra academia (403). Esta entrega não mudou isso.

## Verificação

- Suíte unitária inteira do backend, local: 17.936 passaram; 1 falhou (`test_sem_mojibake`, por arquivos locais não versionados que não existem no CI).
- Bateria dirigida do backend: 958 passaram.
- Em 21/11/2026, bateria dirigida das academias: 475 passaram.
- Frontend: 281 testes Vitest do lote passaram; `tsc` sem erro. Em 21/11/2026, Vitest: 30 passaram.
- Testes de ponta a ponta do Modo Treino (`frontend/e2e/workout-resilience`, Playwright, projeto `chromium-390`), executados fora do CI: 27 passaram, incluindo os dois cenários novos (com e sem a permissão).
- Não verificado: celular real; SQL novo contra MySQL real, que roda só no CI com o rótulo `ci-completo`.

## Atualização de 22/11/2026 (lote 32, #246: paginação opcional)

`GET /equipment-exercises` (`api/app/equipment_exercises.py::list_equipment_exercises`) aceita `limit` (de 1 a `EQUIPMENT_EXERCISES_MAX_LIMIT`, hoje 1000) e `offset` (maior ou igual a 0), ambos opcionais.

- Sem os dois parâmetros, o SQL não tem `LIMIT` e a resposta é a mesma de antes.
- Com `offset` e sem `limit`, o `LIMIT` usa o teto de 1000.
- A ordenação (`ORDER BY eq.name, pg.name, ec.name, eqex.id`) é a mesma, então a paginação é estável.

Impacto: clientes atuais não mudam. Nenhum código de `frontend/src` envia `limit` ou `offset` para esta rota (conferido por busca em 22/11/2026). Não medido: volume de resposta por grupo em produção.

## Atualização de 23/11/2026 (lote 34, #246: recarga falha e acréscimo)

**Catálogo com recarga falha.** O catálogo continua visível quando a recarga da lista da academia falha havendo dados anteriores. A decisão é pela existência de dados (`loadedGymItems` em `frontend/src/lib/equipment-catalog-add.ts`), não pelo sucesso da última consulta. Fecha o item (b) de "Limites e pendências".

**Acréscimo com recarga falha.** `catalogAddOutcome` devolve `refresh_failed` quando a recarga falha. O seletor mostra `CATALOG_ADDED_REFRESH_FAILED_MESSAGE` ("O equipamento foi acrescentado à academia, mas não foi possível atualizar a lista agora...") e não dispara a escolha. Antes, esse caso caía no aviso sem vínculo.

**Geração do acréscimo.** `EquipmentSubstituteModal.tsx` compara a geração do início da operação (`openGeneration`, capturada em `onMutate`) com a atual antes de disparar a escolha. Fechar o seletor durante o acréscimo, portanto, não dispara a escolha. O defeito foi achado pelo teste novo `frontend/src/app/(fullscreen)/modo-treino/EquipmentSubstituteModal.test.ts` (5 casos) e corrigido no lote 34; a execução desse teste não foi repetida nesta atualização de documentação.

**Verificação.** `frontend/src/lib/equipment-catalog-add.test.ts` cobre `resolveRecommendedCandidate` e `catalogAddOutcome` (casos `refresh_failed`, escolha e sem vínculo). Estado da execução: relatado pelo lote 34, não reexecutado aqui.

**Pendente de usuário.** Validação em celular real e em navegador, como no ADR 0027.

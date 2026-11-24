# ADR 0035: Receitas como alimento composto, primeira fatia do backend (issue #286)

- **Status**: Aceita. Backend (lotes 37 a 37c) no PR #293 e telas (lote 38) no PR #294, ambos mesclados na `develop`; ajustes do lote 39 na branch `fix/validacao-e-sobras-receitas-l39`; fatia 2, parte 1 (lote 40) na branch `feat/receitas-edicao-l40`, sem commit; fatia 2, parte 2 (lote 46) na branch `feat/receitas-publicas-l46`, sem commit; sobras e revisão (lote 48) na branch `fix/receitas-sobras-l48`, sem commit. Não promovida a produção.
- **Data**: 2026-11-23
- **Relaciona-se a**: [ADR 0034](0034-prioridade-do-dia-no-diario-da-nutricao.md) (formato e seções deste registro); [ADR 0015](0015-progressao-fonte-unica-no-motor.md) (a regra de ter um único dono por cálculo, aplicada aqui aos nutrientes da receita).

## Contexto

- O usuário pediu (#286) cadastrar uma receita com ingredientes informados em peso cru e o peso final pronto, e usá-la no diário como se fosse um alimento. Até esta entrega não havia receita no sistema.
- Escolhas do usuário em 23/11/2026, pelo formulário:
  - ingrediente é só alimento comum, nunca outra receita;
  - publicação como os alimentos de hoje: só quem administra o catálogo promove. Na primeira fatia, receita NÃO é promovida (erro `recipe_promote_not_supported`);
  - a leitura de refeição por IA não casa com receitas;
  - a entrega é em duas fatias.
- Restrições: a regra de cálculo mora num dono só, pela regra permanente dos motores (`docs/motores/FONTES_DA_VERDADE.md`); os relatórios leem só o que foi gravado na refeição; o catálogo e o diário já têm busca, favoritos, porções e snapshot que não devem ser duplicados.

## Alternativas consideradas

- **Tabela própria de receitas, fora do catálogo.** Recusada: exigiria duplicar busca, favoritos, porções, diário e relatórios para um segundo tipo de alimento.
- **Receita como "refeição salva".** Recusada: uma refeição não vira alimento por 100 g e não tem rendimento (peso final ÷ peso cru), que é o dado central da receita.
- **Permitir receita dentro de receita.** Recusada pelo usuário: ingrediente é só alimento comum. Evita ciclos e a necessidade de expandir subreceitas.
- **Promover receita a pública já na primeira fatia.** Adiada pelo usuário para a segunda fatia, com regras próprias de receita pública.
- **Exigir os quatro macros na linha de receita, como no alimento comum.** Recusada no lote 37c: um ingrediente da TACO sem carboidrato nem proteína (óleo de soja) impedia salvar a receita (falha no CI do PR #289, com arroz e óleo). A linha da receita passa a exigir só energia, como a importação externa.

## Decisão

1. **A receita é um alimento composto.** É uma linha de `food_items` com `source='recipe'` (`catalog.FoodSource.RECIPE`, `api/app/domain/nutrition/catalog.py`), com base em gramas, valores por 100 g efetivos e visível só para o dono. Por isso diário, busca, favoritos, porções, snapshot do lançamento e relatórios funcionam como para qualquer alimento. Os relatórios leem só o valor gravado na refeição.
2. **Estrutura nova (migração 125, `api/db/migrations/125_food_recipes.py`).**
   - `food_recipe_versions`: uma versão de uma receita, com peso final, peso cru, rendimento, modo de preparo (`instructions`, MEDIUMTEXT; o modelo aceita até 20000 caracteres), JSON do cálculo (`RecipeResult.to_dict()`, mais `limitation_codes` desde o lote 37b: os códigos das limitações do cálculo e dos ingredientes) e JSON dos efetivos (`RecipeEffective.to_dict()`). Chave única `(food_id, version)`. Chave estrangeira `created_by` → `users` com `ON DELETE CASCADE`: apagar o usuário apaga as suas versões.
   - `food_recipe_ingredients`: ingredientes de uma versão, com a quantidade como foi digitada, a unidade, a quantidade na base e o snapshot do alimento usado. Caem por cascata quando a versão cai.
   - Sem chave estrangeira para o catálogo (`food_items`); chave estrangeira só para `users` e entre as duas tabelas. Os vínculos com alimento e porção são referências lógicas.
3. **Motor dono: `api/app/domain/nutrition/recipe.py`.** Só calcula; a rota grava.
   - Total de cada campo de `catalog.PER_100_FIELDS` = soma, para cada ingrediente, de (quantidade na base × valor por 100 g do snapshot), pelo `meal_math.sum_scaled_fields`.
   - Valor por 100 g da receita pronta = total ÷ peso final × 100, pelo `meal_math.per_reference_from_serving`.
   - Rendimento = peso final ÷ peso cru.
   - Checagem de energia pelo dono existente, `meal_math.atwater_check` (aviso `WARN_ATWATER`, não bloqueia).
   - Ajuste manual: `apply_overrides` devolve os valores efetivos, preserva os calculados e informa quais campos foram substituídos. Valor nulo significa "sem substituição": o campo usa o calculado. A plausibilidade vem de `catalog.validate_food_values`.
   - Constantes (`recipe.py`): `RECIPE_ENGINE_VERSION = "recipe-v1"`, `RECIPE_MAX_INGREDIENTS = diary.MEAL_MAX_ITEMS` (hoje 30; o mesmo teto de itens de uma refeição, por derivação e não por literal), `RECIPE_YIELD_MIN_SHARE = 0.10`, `RECIPE_YIELD_MAX_SHARE = 5.0` (500%), `RECIPE_FINAL_WEIGHT_MAX_G = 20000.0`.
4. **Ingrediente em ml.** Os nutrientes entram normalmente (valor por 100 ml × ml), mas ml não vira massa: o peso cru soma só os gramas, com a limitação `recipe_raw_weight_excludes_ml`; havendo qualquer ingrediente em ml o rendimento não é calculado (fica vazio) e a trava de faixa não se aplica: vale só o teto absoluto `RECIPE_FINAL_WEIGHT_MAX_G`.
5. **Nutriente incompleto não vira zero.** Se algum ingrediente não informa um campo, o campo entra em `incomplete_fields`, o valor por 100 g dele fica `None`, e o total é só um mínimo. Os ingredientes que faltam são listados por campo em `missing_by_field`.
   - A linha da receita no catálogo exige só energia (`catalog.RECIPE_REQUIRED_PER_100_FIELDS = ("energy_kcal",)`, lote 37c). Um macro que nenhum ingrediente informa fica nulo na linha, nunca zero; sem energia, 400 `invalid_food`. Alimento comum continua exigindo os quatro (`catalog.REQUIRED_PER_100_FIELDS`).
6. **Estimativa.** A receita é estimativa pela composição dos ingredientes e pelo rendimento. Não há modelo de retenção ou perda de nutriente no cozimento.
7. **Rotas `/nutrition/recipes` (`api/app/nutrition_recipes.py`, montadas em `api/app/main.py`).** Permissão `nutricao.alimentos`.
   - `POST /nutrition/recipes/preview`: calcula e não grava.
   - `POST /nutrition/recipes`: cria a receita e a versão 1.
   - `GET /nutrition/recipes/{food_id}`: lê a receita atual.
   - `PUT /nutrition/recipes/{food_id}`: grava uma versão nova. Antes da trava, a receita é conferida sem trava (existência, estado, visibilidade e posse); depois a linha do alimento é lida com trava (`FOR UPDATE`) e a mesma conferência é refeita sobre a linha travada. A maior versão também é lida com trava, para que duas gravações simultâneas não usem o mesmo número. Se a inserção ainda violar a unicidade `(food_id, version)`, a resposta é 409 `recipe_version_conflict` (texto no vocabulário), não 500. No corpo, campo de porção ausente preserva a porção gravada; nulo explícito a apaga.
   - `GET /nutrition/recipes`: lista as receitas do próprio usuário.
   - Três formatos de erro: 422 de validação do corpo (formato padrão do FastAPI); 422 tipado `recipe_*`, com rótulo no vocabulário de `api/app/domain/nutrition/labels.py`; 400 com código `invalid_food`. Posse estrita da receita privada: só o dono lê (`GET`) e edita (`PUT`); para qualquer outro usuário, inclusive quem administra o catálogo, a resposta é 404 `food_not_found`, igual à de receita inexistente (sem 403 para receita privada alheia). **Atualizado no lote 46:** receita PÚBLICA é lida por todos (`can_view_recipe`); o `PUT` de receita pública por quem não administra o catálogo responde 403 `recipe_public_edit_forbidden` (`api/app/nutrition_recipes.py`, `ERR_PUBLIC_EDIT_FORBIDDEN_STATUS = 403`). Números do corpo são estritos: booleano e texto numérico são recusados com 422 em quantidade, peso final, porção e substituições (nulo continua aceito nas substituições); a porção tem teto derivado de `RECIPE_FINAL_WEIGHT_MAX_G`. Ids do alimento e da porção no corpo também são estritos (booleano e texto recusados). Erros de quantidade do ingrediente (códigos do dono do diário com sufixo do índice) têm rótulo em português, `Ingrediente <n>: ...`, com `<n>` a partir de 1 (lote 37b); o `field` e o `index` do erro estruturado seguem a partir de 0. As limitações dos ingredientes (ex.: densidade gramas↔ml que `meal_math.resolve_amount` devolve) vêm no campo `limitations` da prévia e da receita, em criar, ler e editar (lote 37b). Na leitura, a lista sai dos `limitation_codes` gravados; versão sem essa chave cai nas limitações do cálculo, sem as dos ingredientes.
   - Sem a migração 125, as rotas de receita respondem 503 citando a 125; o resto da API segue.
8. **Integração com o cadastro comum.**
   - `PUT /nutrition/foods/{id}` de uma receita responde 409 `food_is_recipe` (a edição é pela tela de receita).
   - Arquivar é o `DELETE` que já existe.
   - `exclude_recipes` existe na busca e na lista de alimentos, e a leitura de refeição por IA usa `exclude_recipes=True` (`nutrition_meal_parse_worker.py`).
   - O snapshot do item do diário ganha `recipe_version` e `recipe_version_id`, só para receita (`nutrition_meals.food_snapshot`).
   - Editar a receita não muda refeição já gravada.

## Trade-offs

- Ganha: nenhuma duplicação de busca, favoritos, diário ou relatórios; um único dono para o cálculo; histórico de refeições estável, porque o snapshot congela valores e versão.
- Ganha: como ingrediente só entra alimento comum, o cálculo não tem recursão nem subreceitas para expandir.
- Paga: o cálculo é estimativa; não modela perda ou ganho de nutriente no cozimento, e o rendimento é só uma razão, sem modelo de cozimento.
- Paga: a limitação de 10% a 500% no rendimento é trava de erro de digitação, decidida na implementação e sem fonte bibliográfica, e só se aplica a receita sem ingrediente em ml.
- Paga: na primeira fatia a receita não pode ser pública nem entrar na leitura de refeição por IA.
- Paga (lote 37c): a linha de receita pode guardar macro nulo. Quem lê o catálogo tem que tratar nulo como "não informado", nunca como zero.

## Consequências

- **Dados.** Duas tabelas novas (migração 125). Nenhuma alteração em tabela existente. Apagar um usuário apaga as versões de receita que ele criou, e os ingredientes caem junto com a versão.
- **Contrato.** Rotas novas em `/nutrition/recipes`. As respostas de receita ganham `recipe.limitations` (lote 37b). Campos novos e opcionais no snapshot do diário, só para receita. O `PUT` de alimento responde 409 para receita.
- **Operação.** Ordem de deploy: migração 125 antes do código. Sem a migração, as rotas de receita respondem 503 e o resto segue. Rollback: reverter o código é seguro enquanto nenhuma receita foi criada. Com alguma receita já criada (linha de `food_items` com `source='recipe'`), as tabelas novas ficam; a ordem é: primeiro desativar as receitas (exclusão lógica das linhas de `food_items` com `source='recipe'`, por migração nova, condicional e idempotente, rodada pela pipeline com `RUN_DB_MIGRATIONS=true`, nunca por escrita manual em produção nem por edição da 125); só então reverter o código. O `down` da 125 é destrutivo (apaga o histórico de versões) e é IMPOSTO pelo código: se `food_recipe_versions` tiver alguma linha, ele falha antes de qualquer statement; com a tabela ausente ou vazia, solta os ingredientes e depois as versões. Sem isso, o código antigo leria uma origem que não conhece na busca, na lista e no diário, e o `down` deixaria alimentos de receita sem versão. Refeições já gravadas com receita guardam o snapshot do item (inclusive `recipe_version`) e o diário lê só o gravado; como o código anterior a este lote lê essa refeição não foi medido, então, havendo refeição com receita, tratar na mesma migração de desativação ou medir antes. As telas ainda não existem, mas as rotas ficam expostas assim que o código sobe: qualquer cliente com a permissão `nutricao.alimentos` pode criar receita pela API. Antes de qualquer reversão, confirmar por leitura em produção (SELECT) que não há linha de `food_items` com `source='recipe'`.
- **Mapa.** O domínio de receita tem dono único em `recipe.py`, registrado em `docs/motores/FONTES_DA_VERDADE.md`. Constantes e fórmulas estão em [docs/nutricao/CALCULOS_NUTRI.md](../nutricao/CALCULOS_NUTRI.md), seção "Receitas (alimento composto)".
- **Próximas decisões.** A segunda fatia decide telas, histórico de versões, duplicação, promoção a pública e filtros.

## Telas (lote 38)

Fatia 1 das telas, sobre o backend do lote 37c (branch `feat/receitas-telas-l38`, #286). Sem mudança de backend.

- **Entrou:**
  - rota web `/nutricao/receitas` (`frontend/src/app/(app)/nutricao/receitas/`), entrada "Receitas" na subnavegação (`frontend/src/components/nutrition/NutritionSubnav.tsx`) e no celular (`frontend/src/mobile/screens/telas/NutricaoNav.tsx`);
  - tela do celular `frontend/src/mobile/screens/telas/NutricaoReceitasScreen.tsx`, registrada em `frontend/src/mobile/screens/registry.c.ts`, que reusa `ReceitasClient`;
  - editor `frontend/src/components/nutrition/RecipeEditor.tsx`: ingredientes em peso cru, peso final, prévia, ajuste manual em `overrides` e modo de preparo como texto puro;
  - cliente `previewRecipe`, `createRecipe`, `fetchRecipes`, `fetchRecipe` e `updateRecipe` em `frontend/src/lib/nutrition-api.ts`;
  - busca de ingrediente com `excludeRecipes` (`frontend/src/components/nutrition/FoodSearch.tsx`), para que receita não entre como ingrediente.
- **Regra das telas:** nenhuma conta no frontend. Totais, valores por 100 g, rendimento, energia e avisos vêm da resposta do backend; o código do frontend só converte texto em número.
- **Saída com alterações:** pede confirmação ao cancelar o editor, ao fechar ou recarregar a aba (`beforeunload`) e ao clicar em link interno (lote 40, ver Limites). Trocar de rota pela subnavegação é link interno, então também pede confirmação.
- **Ficou para a fatia 2** (feito no lote 40: versões, "atualizar ingredientes e recalcular", duplicar pelo dono e cadastro de ingrediente novo sem sair da receita; o resto segue): publicar e filtros minhas, públicas e favoritas; rascunho (publicar, filtros e rascunho fechados no lote 46).
- **Dependência:** a tela só funciona com a migração 125 aplicada; sem ela, as rotas respondem 503.

## Ajustes do lote 39 (issue #291 e sobras #290)

- **Teto do ingrediente (superado no lote 40, ver abaixo).** No lote 39 a quantidade usava `diary.ITEM_AMOUNT_MAX` (`api/app/request_models.py::RecipeIngredientIn`), o teto oficial do item do diário, já efetivo pelo motor. O teto do peso final segue `RECIPE_FINAL_WEIGHT_MAX_G`.
- **Editor.** `RecipeEditor.tsx` trata número não finito como inválido e avisa o ajuste manual (`ajuste-invalido`), com o envio interrompido.
- **422 em vez de 500 (geral do app).** Em `api/app/security.py` (`_sem_nao_finitos`, `validation_error_handler`), registrado em `api/app/main.py` (api-core) e `api/app/main_engines.py` (engines), infinito e NaN no corpo viram texto no 422; o formato do FastAPI não muda.
- **Verificação.** Teste novo `api/tests/unit/test_main_erro_validacao_numero_nao_finito.py`, executado pelo Tech Lead e aprovado junto com os testes das rotas de receita e das engines (42 passed). Ele afirma o registro do handler de validação nos dois apps (api-core e engines) e compara o 422 com o do FastAPI padrão. O CI é quem roda a suíte de integração.

## Ajustes do lote 40 (issue #286, fatia 2 parte 1)

Escolhas do usuário (23/11/2026):

- **Teto próprio do ingrediente: `RECIPE_INGREDIENT_AMOUNT_MAX` = 20 kg** (igual a `RECIPE_FINAL_WEIGHT_MAX_G`), maior que o do item do diário (2 kg). Substitui o teto do lote 39. Alternativa recusada: manter o teto do diário. Sem conversão de unidade, o erro segue o do diário (`recipe.ingredient_amount_errors`). `request_models.RecipeIngredientIn` usa a mesma constante.
- **Edição preserva o snapshot.** `PUT` sem `refresh_ingredients` reaproveita o snapshot gravado dos ingredientes iguais aos da versão atual (`recipe.ingredient_key`: alimento, quantidade, unidade, porção); só os novos ou alterados são resolvidos no catálogo de hoje. Alternativa recusada: resolver tudo de novo a cada edição, o que mudaria o cálculo sem pedido do usuário.
- **Atualizar ingredientes e recalcular** = `PUT` com `refresh_ingredients: true`: todos resolvem do catálogo atual.
- **Ingrediente indisponível** (inativo ou sem acesso) continua válido pelo snapshot. A leitura traz `catalog_status` por ingrediente: `current`, `outdated` (valores por 100 g diferentes do gravado) ou `unavailable` (`recipe.ingredient_catalog_status`).
- **Versões:** `GET /nutrition/recipes/{food_id}/versions` (número, criação, peso final, energia efetiva por 100 g), só do dono.
- **Duplicar:** `POST /nutrition/recipes/{food_id}/duplicate` (201), só do dono. Cria receita privada com os snapshots dos ingredientes, os ajustes (`recipe.overrides_from_effective`) e o modo de preparo. Nome: `recipe.copy_name` (sufixo `RECIPE_COPY_SUFFIX`, corte em `RECIPE_NAME_MAX_CHARS` = 255).
- **Telas:** editor com "atualizar ingredientes" e histórico de versões (`RecipeEditor.tsx`); cadastro de ingrediente novo pelo editor, com `onRegister` do `FoodSearch` e o fluxo `components/nutrition/FoodRegistrationFlow.tsx`; "Duplicar" na lista (`ReceitasClient.tsx`). Só exibem o que a API devolve.
- **Edição com trava:** `update_recipe` trava a linha da receita (`FOR UPDATE`) e só depois lê os ingredientes da versão atual com trava (`FOR UPDATE`), para que a base do reuso de snapshot não mude durante a gravação (`api/app/nutrition_recipes.py`).
- **Snapshot inválido:** snapshot gravado nulo ou malformado não é reaproveitado; o ingrediente é resolvido do catálogo de hoje, como novo (`_snapshot_valido`).

Contrato: campo novo `refresh_ingredients` (opcional, padrão falso) no corpo do `PUT`; campo novo `catalog_status` por ingrediente na leitura; rotas `versions` e `duplicate`. Fora do dono, 404 `food_not_found`.

Fica para a parte 2: publicar receita (regras de receita pública e privacidade do modo de preparo), duplicar pública, filtros minhas, públicas e favoritas, e rascunho.

Impacto em produção: nenhum dado. 0 receitas em produção em 23/11/2026 (medido no lote 37c); a mudança de teto só afeta receita nova.

Verificação: este ADR não registra execução. Testes unitários, guarda de fontes e tela no navegador do lote 40 não foram rodados nesta tarefa.

## Lote 46 — publicação e receita pública (issue #286, fatia 2 parte 2)

Branch `feat/receitas-publicas-l46`, sem commit. Não verificado: execução de testes (só roda no CI para integração) e navegador.

Escolhas do usuário:

- **Publicação como a dos alimentos** (23/11/2026): só quem administra o catálogo promove (`POST /nutrition/foods/{id}/promote`, `admin.catalogos:editar`). Antes, a receita era recusada com 409 `recipe_promote_not_supported`; agora passa pelo motor.
- **Modo de preparo público** (24/11/2026): quando a receita é pública, o modo de preparo é visível a todos. Por isso `can_view_recipe` vale também para o modo de preparo.

Regras (dono: `api/app/domain/nutrition/recipe.py`):

- **Requisitos de publicação** (`publish_blockers`): nome, peso final positivo, ao menos um ingrediente e valores por 100 g válidos pela regra do catálogo com `RECIPE_REQUIRED_PER_100_FIELDS` (só energia). Códigos `recipe_publish_name_required`, `recipe_publish_final_weight_required`, `recipe_publish_no_ingredients`, `recipe_publish_invalid_values`; erro vira 422 tipado, com rótulo em `labels.py`.
- **Quem lê** (`can_view_recipe`): a pública (dona NULL) ou a própria. Receita privada alheia é 404 `food_not_found`, inclusive para quem administra o catálogo.
- **Quem edita** (`can_edit_recipe`): privada só pelo dono; pública só por quem administra o catálogo (`administers_public_catalog(..., "editar")`). O autor original sem a concessão não edita. Quem não pode editar a pública recebe 403 `recipe_public_edit_forbidden` (`ERR_PUBLIC_EDIT_FORBIDDEN_STATUS = 403`): a pública existe para todos, então não há o que esconder.
- **Quem duplica**: a pública ou a própria; a cópia é sempre privada do chamador, com `copy_name`.
- **Filtros** (`GET /nutrition/recipes?scope=`): `mine` (padrão), `public`, `favorites`. Sem ingredientes na lista.
- **Ingrediente privado do autor numa receita pública**: outros usuários veem só o snapshot gravado; `catalog_status` sai `unavailable` para quem não é o autor. Nenhum outro usuário acessa o alimento privado do autor.
- **Rascunho**: só no aparelho (`localStorage`, chave `muscle_fit:receita-rascunho:{userId}:{recipeId|new}`, em `RecipeEditor.tsx`). Não vai ao servidor, não é compartilhado entre aparelhos e não existe em modo leitura.

Alternativas consideradas:

- **Rascunho no servidor.** Recusado: exigiria tabela nova e sincronização para um ganho pequeno; o rascunho é só o estado do formulário.
- **Permitir edição de pública pelo autor original.** Recusado: a receita pública é do catálogo; quem a publica perde a edição quando sai da administração.
- **Publicar só com o modo de preparo privado.** Recusado pelo usuário em 24/11/2026.

Trade-offs:

- Ganha: uma só regra de publicação, para alimento e receita, no motor de `promote`; a tela não decide nada.
- Paga: pela regra de leitura, o administrador vê a linha de alimento privado de outro usuário (já era o comportamento dos alimentos privados); ele não lê o modo de preparo nem edita a receita.

Consequências:

- **Contrato.** `GET /nutrition/recipes?scope=public|favorites`, `POST /nutrition/recipes/{food_id}/duplicate` para pública, `PUT` com 403 para pública alheia, `POST /nutrition/foods/{id}/promote` aceita receita.
- **Dados.** Sem migração nova nesta parte. Publicar altera a linha de `food_items` (`owner_user_id` vira NULL) e as porções.

Impacto em produção: 0 receitas em produção (medido em 23/11/2026, lote 37c; receitas ainda não promovidas). Nada muda para alimentos comuns, exceto que `can_promote_food` (em `api/app/nutrition_access.py`) passa a valer para linha de receita. Nenhum dado existente muda de forma.

## Lote 48 — sobras da #290 e revisão (issue #290, branch `fix/receitas-sobras-l48`, sem commit)

Não verificado: execução de testes unitários nesta entrega (a integração só roda no CI) e navegador.

O que mudou (dono `api/app/domain/nutrition/recipe.py`, salvo indicação):

- `merged_warnings`: junta avisos do cálculo e dos efetivos numa lista só; a prévia e a receita (`api/app/nutrition_recipes.py`) usam essa função.
- `is_valid_id` e `RECIPE_ID_MAX` (2.147.483.647, teto do INT com sinal): id de ingrediente ou porção acima disso responde "não encontrado" em vez de erro de banco.
- Paginação da lista: `GET /nutrition/recipes?limit=&offset=`, com `RECIPE_LIST_DEFAULT_LIMIT` = 500, `RECIPE_LIST_MAX_LIMIT` = 500 e `RECIPE_LIST_MAX_OFFSET` = 100.000. Sem parâmetros vale o comportamento anterior (até 500, do início).
- `ERR_PUBLISH_ARCHIVED` (`recipe_publish_archived`): receita arquivada não publica. Segue o formato tipado dos demais `recipe_publish_*`.
- `ERR_VERSION_CONFLICT` (`recipe_version_conflict`) e `ERR_INGREDIENT_*` (`recipe_ingredient_not_found`, `recipe_ingredient_is_recipe`, `recipe_ingredient_portion_not_found`, `recipe_ingredient_amount_out_of_range`), com `:<índice>`.
- Limitação por ingrediente sai do motor como `<código>:<índice>`, e `api/app/domain/nutrition/labels.py` rotula citando o ingrediente.
- `limitation_codes` deixa de ser gravado. A leitura de versão antiga que ainda tem a chave continua funcionando (`nutrition_recipes.py`, cai em `limitations` quando a chave falta).
- `nutrition_access.can_edit_food` (`api/app/nutrition_access.py`) delega a `recipe.can_edit_recipe`. Antes havia regra paralela; agora só há uma.
- Promoção de receita exige linha ativa e tabelas; sem as tabelas responde 503.
- Frontend: o rascunho local guarda a versão e é apagado no logout (`frontend/src/lib/app-logout.ts`); Publicar fica desabilitado com edição pendente; a confirmação de publicação cita ingredientes e modo de preparo (`RecipeEditor.tsx`).

Decisões de não mudar, com o motivo:

1. **`_has_tables` (`api/app/nutrition_schema.py`) guarda só cache positivo.** Ausência não é cacheada, para que a migração 125 aplicada depois não exija reinício. Consequência: após um `down` da 125, as Lambdas que já viram a tabela precisam ser reiniciadas, porque o cache positivo continua.
2. **A validação de tabela existente da 125 não confere chaves estrangeiras.** A migração 125 já foi aplicada no DEV e não se edita (checksum). A lacuna segue registrada em "Não medido" no mapa.
3. **Administrador do catálogo vê nome e valor por 100 g de receita privada alheia.** É a regra já existente da linha de alimento privado, e quem administra é quem publica. Ele não vê ingredientes nem modo de preparo (teste de rota) e não edita nem promove receita de outro usuário sem a concessão.
4. **`DELETE /nutrition/foods/{id}` e as porções seguem aceitando receita.** A tela de receita arquiva por esse `DELETE`; uma rota nova duplicaria o que já existe.

## Impacto em produção

Lote 38: só tela nova; nenhuma mudança de backend nem de migração. Os números de produção são os medidos acima para o lote 37c.

Medido pelo Tech Lead em 23/11/2026, só leitura (SELECT), direto na produção: 665 alimentos, nenhum com origem de receita (`source='recipe'`), 10 alimentos privados, e as duas tabelas novas ainda não existem. A medição anterior, na cópia de produção do banco de desenvolvimento (carga de 22/11/2026), dava 664 alimentos; a cópia só traz o usuário escolhido e o catálogo público. Logo nenhum alimento, refeição ou meta existente muda. O ajuste do lote 37b (limitações na resposta e rótulo de ingrediente a partir de 1) também não tem efeito em produção: não há receita nem tabela de receita, então nenhuma versão antiga fica sem os avisos. O lote 37c (obrigatório da linha de receita só energia) tem impacto nulo: 0 receitas em produção, medido em 23/11/2026 por SELECT; a regra só se aplica a linha com `source='recipe'`. Que alimento comum não muda de formato em nenhuma resposta é garantido por testes unitários (snapshot de alimento comum inalterado; SQL de busca idêntico sem o filtro de receitas), a confirmar no CI. O script de cópia de produção para o desenvolvimento (`scripts/prod_to_dev.py`) copia só as receitas do próprio usuário.

Conferência depois do deploy (na promoção, só leitura): (b) as duas tabelas existem em `information_schema.TABLES` com engine InnoDB; (c) contagem de `food_items` com `source='recipe'` (esperado 0 logo após o deploy); (a) prova secundária: `GET /nutrition/recipes` responde 200, e não 503 `food_recipe_tables_missing`, o que exige um usuário autenticado com a permissão `nutricao.alimentos`.

**Não medido:** a migração 125 nunca foi aplicada em banco; a integração nova nunca foi executada (só roda no CI); concorrência na unicidade da versão (a trava `FOR UPDATE` sob concorrência real em MySQL); leitura de refeição com receita pelo código anterior a este lote (snapshot com origem de receita), não medida; a validação de tabela da 125 não confere chaves estrangeiras nem a regra de exclusão (lacuna do molde 102), o que só importa para tabela criada à mão antes da migração.

## Limites

- **Fechado no lote 46 (parte 2 da fatia 2):** promover a pública (com o modo de preparo público); duplicar receita pública; filtros minhas, públicas e favoritas; rascunho retomável (só no aparelho).
- **Confirmação ao sair com alterações (atualizado no lote 46):** existe no código do checkout (`frontend/src/components/nutrition/RecipeEditor.tsx`: `beforeunload` por `dirty`, linhas 178 a 185; clique em link interno com alterações por listener de `click` em captura no `document`, linhas 187 a 206; cancelar abre "Sair sem salvar?", linhas 308 e 641). Conferido por busca no código; não verificado no navegador.
- Feito no lote 40 (seção própria): histórico de versões, atualizar ingredientes e recalcular, e duplicar pelo dono.
- O diário soma seis nutrientes. Açúcares, gordura saturada e gordura trans da receita ficam nos valores por 100 g e no snapshot, sem entrar no total do diário nesta fatia.
- Risco residual: violação da unicidade de `(food_id, version)`, mitigada por trava de linha e, em último caso, respondida com 409; a trava sob concorrência real não foi medida.
- Corpo com número infinito devolvia 500 por um problema geral do app na serialização do erro de validação; corrigido no lote 39 (issue #291): agora 422, e o valor não chega ao motor.
- A integração não roda localmente; só o CI a executa.
- Posse estrita da receita privada: quem administra o catálogo não lê nem edita receita privada de outro usuário; a resposta é 404 `food_not_found`, sem 403 (ver Decisão 7). Receita pública (lote 46): lida por todos; edição só por quem administra o catálogo (403 `recipe_public_edit_forbidden` para os demais).
- Linha de alimento: quem administra o catálogo ainda vê, pela busca de alimentos privados e por `GET /nutrition/foods/{id}`, a linha de alimento da receita de outro usuário (nome e valores por 100 g, sem ingredientes, versões nem modo de preparo). É a regra já existente dos alimentos privados; essa pessoa não pode editar, excluir nem promover a receita.
- Erros de quantidade do ingrediente: o número na mensagem (`Ingrediente <n>`) começa em 1 (lote 37b); o `index` do erro estruturado começa em 0.
- Receita cujo ingrediente não informa um macro aparece na prévia com o campo incompleto e é salva com esse campo nulo (lote 37c). Sem energia, 400 `invalid_food`; a substituição manual do campo continua disponível.
- Com qualquer ingrediente em ml, o rendimento não é calculado e a trava de rendimento não se aplica; vale só o teto absoluto do peso final.

## Verificação

Arquivos de teste da entrega (este ADR não registra resultado de execução):

- `api/tests/unit/test_nutrition_recipe.py` (novo);
- `api/tests/unit/test_nutrition_recipes_routes.py` (novo);
- `api/tests/unit/test_migration_125_plano.py` (novo);
- `api/tests/integration/dataapi/test_nutrition_recipes_dataapi.py` (novo; só roda no CI).

Não verificado neste ADR: a execução desses testes; a aplicação da migração em banco real; qualquer tela. Lote 37c: a regra da linha só energia tem teste unitário em `api/tests/unit/test_nutrition_recipes_routes.py` (`test_ingrediente_sem_carboidrato_e_proteina_grava_nulo_e_lista_como_incompleto` e `test_valores_efetivos_sem_energia_dao_400_do_catalogo_e_nada_grava`), mas a execução desses testes não foi feita aqui. O que segue sem prova é a integração com MySQL real, só no CI. O motivo do ajuste foi a falha que o CI encontrou no PR #289 (teste de integração `test_nutrition_recipes_dataapi.py`, com arroz e óleo), não reexecutada aqui.

## Lote 49 — nota sobre o rascunho no logout e na sessão expirada

Não verificado: execução dos testes nesta entrega.

- O rascunho de receita (`localStorage`, chave pelo id do usuário; `frontend/src/components/nutrition/RecipeEditor.tsx`) não é regravado durante o logout: há um guarda "Logout em curso", para não regravar o que o logout acabou de apagar.
- O rascunho também é apagado quando a sessão expira (401): `frontend/src/lib/api.test.ts`, teste "401 apaga os rascunhos de receita junto com a sessão". O 401 que não é de logout segue o redirect de login do `apiFetch`.
- A limpeza dos rascunhos fica no módulo folha `frontend/src/lib/recipe-draft-storage.ts` (`clearRecipeDrafts`, sem imports), usado por `api.ts` no 401 e por `app-logout.ts`. É folha para não criar ciclo de import com `api.ts`.
- Regra de aparelho, sem servidor: o rascunho não sai do aparelho e não tem tabela. Ver também a nota do lote 48 sobre `frontend/src/lib/app-logout.ts`.

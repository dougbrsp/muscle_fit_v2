# ADR 0035: Receitas como alimento composto, primeira fatia do backend (issue #286)

- **Status**: Aceita. Branch `feat/receitas-backend-l37`; o ajuste do lote 37b está em `feat/receitas-backend-l37b`; o do lote 37c (obrigatório da linha de receita só energia) está em `feat/receitas-backend-l37c`. Não publicada nem promovida a produção.
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
   - Três formatos de erro: 422 de validação do corpo (formato padrão do FastAPI); 422 tipado `recipe_*`, com rótulo no vocabulário de `api/app/domain/nutrition/labels.py`; 400 com código `invalid_food`. Posse estrita: só o dono lê (`GET`) e edita (`PUT`); para qualquer outro usuário, inclusive quem administra o catálogo, a resposta é 404 `food_not_found`, igual à de receita inexistente (não há 403 nas rotas de receita). Quando a receita puder ser pública (segunda fatia), a regra de leitura será revista. Números do corpo são estritos: booleano e texto numérico são recusados com 422 em quantidade, peso final, porção e substituições (nulo continua aceito nas substituições); a porção tem teto derivado de `RECIPE_FINAL_WEIGHT_MAX_G`. Ids do alimento e da porção no corpo também são estritos (booleano e texto recusados). Erros de quantidade do ingrediente (códigos do dono do diário com sufixo do índice) têm rótulo em português, `Ingrediente <n>: ...`, com `<n>` a partir de 1 (lote 37b); o `field` e o `index` do erro estruturado seguem a partir de 0. As limitações dos ingredientes (ex.: densidade gramas↔ml que `meal_math.resolve_amount` devolve) vêm no campo `limitations` da prévia e da receita, em criar, ler e editar (lote 37b). Na leitura, a lista sai dos `limitation_codes` gravados; versão sem essa chave cai nas limitações do cálculo, sem as dos ingredientes.
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
- **Saída com alterações:** pede confirmação ao cancelar o editor e ao fechar ou recarregar a aba (`beforeunload`). Trocar de rota pela subnavegação ainda não pede confirmação.
- **Fica para a fatia 2:** versões com "atualizar ingredientes e recalcular"; duplicar; publicar e filtros minhas, públicas e favoritas; rascunho; cadastrar ingrediente novo sem sair da receita (o editor não passa `onRegister` ao `FoodSearch`); confirmação ao trocar de rota com alterações.
- **Dependência:** a tela só funciona com a migração 125 aplicada; sem ela, as rotas respondem 503.

## Impacto em produção

Lote 38: só tela nova; nenhuma mudança de backend nem de migração. Os números de produção são os medidos acima para o lote 37c.

Medido pelo Tech Lead em 23/11/2026, só leitura (SELECT), direto na produção: 665 alimentos, nenhum com origem de receita (`source='recipe'`), 10 alimentos privados, e as duas tabelas novas ainda não existem. A medição anterior, na cópia de produção do banco de desenvolvimento (carga de 22/11/2026), dava 664 alimentos; a cópia só traz o usuário escolhido e o catálogo público. Logo nenhum alimento, refeição ou meta existente muda. O ajuste do lote 37b (limitações na resposta e rótulo de ingrediente a partir de 1) também não tem efeito em produção: não há receita nem tabela de receita, então nenhuma versão antiga fica sem os avisos. O lote 37c (obrigatório da linha de receita só energia) tem impacto nulo: 0 receitas em produção, medido em 23/11/2026 por SELECT; a regra só se aplica a linha com `source='recipe'`. Que alimento comum não muda de formato em nenhuma resposta é garantido por testes unitários (snapshot de alimento comum inalterado; SQL de busca idêntico sem o filtro de receitas), a confirmar no CI. O script de cópia de produção para o desenvolvimento (`scripts/prod_to_dev.py`) copia só as receitas do próprio usuário.

Conferência depois do deploy (na promoção, só leitura): (b) as duas tabelas existem em `information_schema.TABLES` com engine InnoDB; (c) contagem de `food_items` com `source='recipe'` (esperado 0 logo após o deploy); (a) prova secundária: `GET /nutrition/recipes` responde 200, e não 503 `food_recipe_tables_missing`, o que exige um usuário autenticado com a permissão `nutricao.alimentos`.

**Não medido:** a migração 125 nunca foi aplicada em banco; a integração nova nunca foi executada (só roda no CI); concorrência na unicidade da versão (a trava `FOR UPDATE` sob concorrência real em MySQL); leitura de refeição com receita pelo código anterior a este lote (snapshot com origem de receita), não medida; a validação de tabela da 125 não confere chaves estrangeiras nem a regra de exclusão (lacuna do molde 102), o que só importa para tabela criada à mão antes da migração.

## Limites

- **Fica para a segunda fatia:**
  - telas: a fatia 1 está no lote 38 (seção "Telas (lote 38)"); o restante segue nesta lista;
  - histórico de versões com "atualizar ingredientes e recalcular";
  - duplicar receita;
  - promover a pública, com as regras de receita pública;
  - filtros minhas, públicas e favoritas;
  - rascunho retomável.
- O diário soma seis nutrientes. Açúcares, gordura saturada e gordura trans da receita ficam nos valores por 100 g e no snapshot, sem entrar no total do diário nesta fatia.
- Risco residual: violação da unicidade de `(food_id, version)`, mitigada por trava de linha e, em último caso, respondida com 409; a trava sob concorrência real não foi medida.
- Corpo com número infinito ainda devolve 500 por um problema geral do app na serialização do erro de validação. Essa falha é anterior a este lote, e o valor não chega ao motor.
- A integração não roda localmente; só o CI a executa.
- Posse estrita: quem administra o catálogo não lê nem edita receita privada de outro usuário; a resposta é 404 `food_not_found`, sem 403 (ver Decisão 7).
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

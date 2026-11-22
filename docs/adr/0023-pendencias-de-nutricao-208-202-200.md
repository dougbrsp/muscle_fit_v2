# ADR 0023: Pendências de nutrição das issues #208, #202, #200 e #229 (busca por marca, listagem por letra, voltar do cabeçalho, guardas e mensagem do 422)

- **Status**: Aceita. Branch `fix/nutricao-pendencias-208-202-200-229`. Não publicada nem promovida a produção.
- **Data**: 2026-11-20
- **Complementa**: ADR 0016 (kcal informada do Intervals ativa; é o contexto da guarda `padrao_modo_caloria`, #138 e #200) e o §48 do mapa (catálogo de alimentos, #195). Nenhuma decisão anterior é substituída.

## Contexto

As quatro issues são sobras de entregas anteriores do catálogo de alimentos (#195, #138, #194, #196 e #224/#178):

1. **#208, busca por marca e listagem por letra.** A consulta por marca (`brand LIKE`) não tinha ordem própria: o lote de candidatos saía em ordem de cadastro (`ORDER BY id`), e um alimento de cadastro antigo podia ser cortado do lote. Uma busca que mistura nome e marca ("whey growth") não encontrava o item, porque cada termo era testado só contra o nome. A listagem por letra aceitava `offset` sem teto (um deslocamento grande tendia a erro 500 do banco) e tinha a letra inicial fixa no frontend. Os rótulos dos baldes e a invalidação da lista por letra passam a ter um dono só (ver Decisão).
2. **#202, navegação mobile e itens da entrega original.** A origem da navegação era um booleano de módulo (`navegouDentroDoApp`), sem teste do clique de voltar do cabeçalho (o projeto não monta o `MobileShell` em DOM nos testes). Havia também lacunas de teste de `energy_params` e de retrato de acesso.
3. **#200, guarda do modo de caloria e cobertura do scanner.** A regex `padrao_modo_caloria` não acusava a atribuição quebrada em duas linhas nem `CalorieMode.X.value`. Não estava claro, para quem lê a guarda, o que o scanner varre no frontend.
4. **#229, mensagem do erro ao salvar.** A tela web de geração (`frontend/src/app/(app)/planos-treino/GenerateDraftModal.tsx`) mostrava a mensagem genérica, sem a mensagem do 422 que o servidor devolve.

Restrições: sem migração (o banco de produção não muda); o SQL novo não tem integração local (o CI roda a integração); a tela não pode ter regra de busca nem de ordem (`docs/motores/FONTES_DA_VERDADE.md`, regra "Regras só nos motores").

Medição de 20/11/2026, por leitura (só SELECT): `food_items` com 660 alimentos, 33 marcas distintas, a maior marca com 4 itens. O cadastro é pequeno, e a consulta por marca varre a tabela sem índice.

## Alternativas consideradas

- **Índice ou coluna normalizada de marca (#208).** Descartada agora: exigiria migração para um cadastro de 660 linhas, em que a varredura é barata. O gatilho de revisão está no comentário `shortcut:` de `api/app/nutrition_foods.py` (L161-162): criar índice ou coluna quando o cadastro passar de ~50 mil linhas ou a busca por marca passar de ~200 ms.
- **Termo só de símbolos consultar a marca (#208).** Descartada: a normalização do nome zera o termo, não há o que casar. A rota responde vazio antes de qualquer consulta (`nutrition_foods.py` L390-391, com `prefix_pattern` devolvendo None para normalização vazia).
- **Voltar do cabeçalho testado com o `MobileShell` montado.** Descartada: o projeto não monta o shell em DOM nos testes. A decisão foi para função pura (`acaoVoltarCabecalho`), e o shell só executa o resultado.
- **Busca de marca com os termos na mesma consulta do nome (sem consulta própria).** Não adotada: a consulta própria garante que a marca não seja cortada do lote do nome (`brand_search_sql`, docstring).

## Decisão

1. **Dono da busca e da listagem.** `api/app/domain/nutrition/food_text.py`:
   - `BRAND_TOKENS_MAX = 5` (L76) e `brand_token_patterns(query)` (L270-276): cada termo casa no nome normalizado OU na marca, e ao menos um na marca; vazio com menos de 2 termos.
   - `LIST_OFFSET_MAX = 10_000` (L86), `DEFAULT_LIST_BUCKET = LIST_BUCKETS[0]` (L88), `OTHER_BUCKET_LABEL` (L89) e `LIST_BUCKET_LABELS` (L90), o texto de exibição de cada balde.
2. **Consumidores só leem.** `api/app/nutrition_foods.py`:
   - `search_foods` (L405-413) monta a consulta por marca com `brand_token_patterns` e `brand_search_sql(..., tokens=N)` (L153-173). O lote de candidatos de marca é ordenado por `name_normalized, id` (antes: `id`), e a consulta é própria, para a marca nunca ser cortada do lote do nome.
   - `list_foods_by_letter` (L457-486): `letter` ausente vale `DEFAULT_LIST_BUCKET` (L468); `offset` acima de `LIST_OFFSET_MAX` responde 422 pelo `Query(le=...)` (L461); a resposta ganha `letter_labels` (L486).
   - `FoodsAlphabet.tsx` (`frontend/src/components/nutrition/`): a primeira requisição vai sem `letter` (L19-23) e a aba ativa vem da resposta (L39); o rótulo vem de `letter_labels`, com o código como reserva; a constante `INITIAL_LETTER` saiu; só `aria-pressed` (L61); sem ref escrita no render.
3. **Invalidação em um lugar.** `frontend/src/lib/nutrition-food-queries.ts::invalidateFoodQueries` (L5-10) invalida a busca e a lista por letra. Usada por `FoodRegistrationFlow.tsx` (L129), `FoodRegistrationFinish.tsx` (L49), `FoodSearch.tsx` (L97), `FavoritesManager.tsx` (L155) e `MealEditor.tsx` (L304).
4. **Origem da navegação com estado.** `frontend/src/mobile/layout/nav-active.ts`: o booleano `navegouDentroDoApp` virou o estado `OrigemRota = {primeira, atual, anterior}` (L26); `registrarRota` é idempotente (L32-36, o efeito em dobro do StrictMode não muda nada); `origemEhInterna` (L39-40) exige `anterior` não nulo e `atual` diferente de `primeira`. Voltar à tela em que o aplicativo foi aberto vale como entrada direta, então cancelar o editor de refeição faz `replace` para `/nutricao/` e nunca usa o voltar do histórico. `acaoVoltarCabecalho` (L70-81) decide o clique de voltar do cabeçalho; `MobileShell.tsx` (L169) só executa a decisão.
5. **Guarda.** `api/tests/unit/test_guarda_fontes_da_verdade.py`:
   - `padrao_modo_caloria` (L2139-2156): a regex acusa `CalorieMode.X.value` e o literal de modo atribuído fora do dono (L2146-2152). A atribuição quebrada em duas linhas (#200) é acusada pela varredura, não pela regex: a linha terminada em `=`, `= (` ou `= \` é testada junto com a seguinte (`junta_continuacao`, `ocorrencias()` em L2800-2816), e a ocorrência cai na linha da atribuição, sem contagem dupla. As três formas são `=` no fim da linha (TypeScript), `= (` e `= \` (Python). Limite: só a linha seguinte é juntada. Pares no autoteste (L3649-3662) e testes de ponta a ponta sobre arquivos temporários (L3986-4016). Allowlist sem mudança.
   - `catalogo_alimentos` (L1203-1223): `LETTERS` e `LIST_BUCKETS` redefinidos (`tuple(...)`, `list(...)`, `Array.from(...)`, literal de texto, `string.ascii_uppercase`) e `OTHER_BUCKET = "..."` acusados fora do dono (L1215-1219). Pares no autoteste (L3494-3505). Allowlist sem mudança.
   - Cobertura do scanner (`arquivos_varridos`, L2737-2766): `frontend/src/**/*.ts` e `*.tsx`; ficam de fora `*.test.ts(x)`, `*.spec.ts(x)` (`_TESTE_TS`, L53), `__tests__` (`_PASTAS_IGNORADAS`, L52), o que está em `frontend/` fora de `src/` e os `.js`/`.mjs` (`_linguagem`, L2671-2678, só reconhece `.ts`/`.tsx`).
6. **Acesso.** `DELETE /nutrition/foods/{food_id}` saiu do retrato congelado `api/tests/unit/data/access_retrato_guards_antigos.json` e entrou em `ROTAS_NOVAS_POS_RETRATO` como `ROTA_NOVA_ALIMENTOS_EXCLUIR` (`api/tests/unit/test_access_guarda_cobertura_rotas.py`, L86 e L122).
7. **Testes novos.** `api/tests/unit/test_nutrition_foods_pendencias_208.py` e `api/tests/unit/test_nutrition_energy_params_nao_adaptativo.py` (este último fixa a regra já existente em `api/app/domain/nutrition/plan.py`: em plano não adaptativo com `energy_params`, `watch_calorie_mode` "unknown" devolve `param_not_applicable` e o valor padrão passa).
8. **Mensagem do 422 na tela web (#229).** `GenerateDraftModal.tsx` (L379) usa `planSaveErrorMessage` (`frontend/src/lib/plan-save-error.ts`), o mesmo da tela mobile (`GerarPlanoView.tsx`, L219).

Resultado por item da issue:

| Issue | Item | Resultado | Onde |
|---|---|---|---|
| #208 | Busca por marca cortada pela ordem de cadastro | Corrigido: ordem por nome normalizado e id | `nutrition_foods.py` L173 |
| #208 | Busca que mistura nome e marca não encontra o item | Corrigido | `food_text.py` L270-276; `brand_search_sql(tokens=N)` |
| #208 | Termo só de símbolos | Descartado consultar a marca: a rota responde vazio sem consulta | `nutrition_foods.py` L390-391 |
| #208 | Índice ou coluna normalizada de marca | Descartado nesta entrega (sem migração; 660 alimentos, 33 marcas, maior marca com 4 itens); gatilho no comentário `shortcut:` | `nutrition_foods.py` L161-162 |
| #208 | Teto do `offset` da listagem | Corrigido: 422 acima de `LIST_OFFSET_MAX` | `food_text.py` L86; `nutrition_foods.py` L461 |
| #208 | Letra inicial fixa no frontend | Corrigido: primeira requisição sem letra; aba vem da resposta | `FoodsAlphabet.tsx` L19-39 |
| #208 | Invalidação da lista por letra | Corrigido: função única com a busca | `nutrition-food-queries.ts` L5-10 |
| #208 | Rótulo do balde escrito à mão | Corrigido: `letter_labels` vindo do dono | `food_text.py` L89-90; `nutrition_foods.py` L486 |
| #208 | Acessibilidade e ref escrita no render | Corrigido: só `aria-pressed`; sem ref | `FoodsAlphabet.tsx` L61 |
| #208 | `LETTERS` fora do dono | Corrigido: padrão `catalogo_alimentos` | guarda L1215-1219 |
| #208 | Docstring desatualizada | Corrigido | `nutrition_foods.py` L154-160 |
| #208 | Script do replay de impacto não versionado | Não feito (pendência) | — |
| #208 | Tela real web, mobile e teclado não verificados na entrega original | Continuam não verificados | — |
| #208 | e2e de layout mobile | Executado (ver Verificação), com falhas não atribuídas | `frontend/e2e/mobile-layout` |
| #208 | Fake × collation real | Continua não verificado (integração no CI) | — |
| #202 | Item 1: retrato de acesso | Corrigido | `access_retrato_guards_antigos.json`; `test_access_guarda_cobertura_rotas.py` L86, L122 |
| #202 | Item 2: flag de origem | Corrigido: estado `{primeira, atual, anterior}`. Voltar à tela de abertura conta como entrada direta, e cancelar o editor faz `replace` para `/nutricao/`. Consequência aceita em Limites (h) | `nav-active.ts` L26-40 |
| #202 | Item 3: teste do voltar do cabeçalho | Corrigido por função pura, porque o shell não monta em DOM | `nav-active.ts` L70-81; `MobileShell.tsx` L169 |
| #202 | Item 4: e2e mobile | Executado (ver Verificação) | — |
| #202 | Item 5: teste de `energy_params` | Corrigido | `test_nutrition_energy_params_nao_adaptativo.py` |
| #202 | Item 6: foto do prato reduzida em memória | Não feito (pendência): a redução acontece em `readMealPhoto` (`frontend/src/lib/nutrition-api.ts` L458) e o arquivo original chega ao rascunho por `frontend/src/components/nutrition/MealParseModal.tsx` (L106) | — |
| #202 | Item 7: iPhone real | Não verificável nesta entrega | — |
| #200 | Atribuição do modo quebrada em duas linhas | Corrigida pela varredura: a linha terminada em `=`, `= (` ou `= \` é testada junto com a seguinte, e a ocorrência cai na linha da atribuição. Limite: só a linha seguinte é juntada | guarda L2800-2816; pares L3649-3662; testes L3986-4016 |
| #200 | `CalorieMode.X.value` atribuído fora do dono | Corrigido na regex, com pares no autoteste | guarda L2146-2152, L3649-3662 |
| #200 | Cobertura do frontend | Confirmada por leitura do scanner (o que é e o que não é varrido está na Decisão, item 5) | guarda L52-53, L2737-2766 |
| #200 | Estender a varredura a `.js`/`.mjs` e a `frontend/` fora de `src` | Não feito (pendência) | `_linguagem` L2671-2678 |
| #229 | Mensagem do 422 na tela web | Corrigido | `GenerateDraftModal.tsx` L379 |
| #229 | Comentários desatualizados | Corrigidos (o comentário de `exercise-swap-options.ts` L147 agora confere com `workout-plan-payload.ts` L54) | — |

## Trade-offs

- Ganha: a busca não perde item por ordem de cadastro nem por termos divididos entre nome e marca; a resposta da listagem tem contrato explícito para deslocamento fora da faixa; a tela deixa de carregar regra de letra, rótulo e invalidação.
- Ganha: nenhuma migração; o SQL novo fica restrito à consulta de marca.
- Paga: a consulta de marca é uma varredura sem índice. Não há medição de tempo nem de `EXPLAIN` (cadastro de 660 linhas).
- Paga: a busca que mistura nome e marca pode devolver um alimento a mais. Para busca de um termo só o resultado não muda; o resultado só cresce, nunca diminui.
- Paga: a guarda não cobre todos os nomes novos de constante (ver Limites).

## Consequências

- **Dados.** Sem migração. Nenhum dado gravado muda no deploy.
- **Contrato.** `GET /nutrition/foods` aceita `letter` ausente (primeiro balde) e responde 422 para `offset` acima de `LIST_OFFSET_MAX`; a resposta ganha `letter_labels` (aditivo: o aplicativo antigo ignora o campo).
- **Operação.** Cadastro acima de ~50 mil linhas ou busca por marca acima de ~200 ms é o gatilho para índice ou coluna normalizada de marca (comentário `shortcut:`).
- **Próximas decisões.** Novo balde, rótulo ou teto da listagem entra em `food_text.py`, nunca em tela, SQL ou guarda duplicada. Ampliar a varredura do scanner para `.js`/`.mjs` e `frontend/` fora de `src` é decisão de guarda, com revisão do guardião.
- **Mapa.** `docs/motores/FONTES_DA_VERDADE.md` registra os consumidores e as proibições do catálogo (§48), os casos novos da guarda (§104.1), a cobertura do scanner (§5) e o bloco "Lote 6 de 20/11/2026 (#208, #202, #200 e #229)" em "Impacto em produção".

## Limites e pendências

- (a) A origem interna da navegação se perde ao recarregar a página. O resultado é ir para `/nutricao/`, sem sair do aplicativo.
- (b) Voltar ao balde inicial depois de trocar de letra refaz uma requisição.
- (c) Os nomes `LIST_OFFSET_MAX`, `DEFAULT_LIST_BUCKET`, `OTHER_BUCKET_LABEL`, `LIST_BUCKET_LABELS` e `BRAND_TOKENS_MAX` não estão na regex `catalogo_alimentos`: a proibição de redefini-los fora do dono é por revisão.
- (d) Pendências: script do replay de impacto (#208, não versionado); foto do prato reduzida em memória (#202, item 6); varredura da guarda para `.js`/`.mjs` e `frontend/` fora de `src` (#200).
- (e) Não verificado: telas web e mobile vistas em navegador manualmente; navegação por teclado; iPhone real; SQL da busca por marca contra o MySQL real (integração no CI); ordem de nomes com pontuação na collation real; tempo e `EXPLAIN` da consulta de marca.
- (f) Falhas do e2e de layout mobile (ver Verificação) não foram atribuídas a esta entrega, porque a suíte não foi executada na `develop` para comparação.
- (g) A junção da varredura cobre só a linha seguinte ao `=` (`junta_continuacao`): valor que comece duas ou mais linhas depois do `=` não é acusado.
- (h) Consequência aceita da origem por entrada direta: quem abriu o aplicativo direto no editor de refeição, saiu e voltou por toque na aba cai em `/nutricao/` ao cancelar, em vez de voltar à aba anterior. A alternativa é arriscar sair do aplicativo.

## Verificação

- Suíte unitária inteira do backend, local, depois da última correção: 17.838 passaram; 1 falhou (`test_sem_mojibake`, por arquivos locais não versionados que não existem no CI).
- Frontend: 329 testes Vitest dos diretórios tocados passaram; `tsc --noEmit` sem erro.
- Testes de ponta a ponta de layout mobile (`frontend/e2e/mobile-layout`, Playwright), executados em navegador fora do CI pela primeira vez: 84 passaram e 47 falharam, em 46 minutos. As falhas se concentram em cenários que dependem de permissões: a navegação principal é fechada por padrão sem permissões (`frontend/src/mobile/layout/primary-nav.ts`) e as respostas simuladas de `/auth/me` desses cenários não trazem as permissões do RBAC (em produção desde 10 a 15/11/2026); exemplos: a barra de abas com 1 item em vez de 5, botões "Gerar novo plano" e "Gerar insight" ausentes. A suíte não foi executada na `develop` para comparação, então não está provado, teste a teste, que nenhuma falha vem desta entrega. Corrigir as fixtures é pendência própria.
- Guarda de fontes da verdade (`api/tests/unit/test_guarda_fontes_da_verdade.py`): a guarda inteira passa, com 485 testes.
- Revisão independente em duas rodadas: a primeira achou que a varredura da guarda não acusava a atribuição em duas linhas e que voltar pelo navegador ao editor aberto por entrada direta ainda saía do aplicativo; os dois foram corrigidos.
- Não verificado: nenhuma tela foi vista em navegador manualmente nem em iPhone; o SQL novo da busca por marca contra o MySQL real (integração no CI).

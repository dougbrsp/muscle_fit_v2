# Fontes do catálogo de alimentos

Proveniência versionada das tabelas de composição que o app embute (plano
[`PLANO_NUTRICAO_2026-10-14.md`](../PLANO_NUTRICAO_2026-10-14.md) §4, §15 D-F e R5;
mapa dos motores [`FONTES_DA_VERDADE.md`](../../motores/FONTES_DA_VERDADE.md) §48).

## TACO 4ª edição — `taco_4a_edicao.xlsx`

| Item | Valor |
|---|---|
| Publicação | Tabela Brasileira de Composição de Alimentos – TACO, 4ª ed. rev. e ampl. |
| Autor | Núcleo de Estudos e Pesquisas em Alimentação (NEPA), Universidade Estadual de Campinas (UNICAMP) |
| Citação (a mesma de `domain/nutrition/catalog.TACO_CITATION` e de cada linha do seed) | Núcleo de Estudos e Pesquisas em Alimentação (NEPA-UNICAMP). Tabela Brasileira de Composição de Alimentos – TACO. 4. ed. rev. e ampl. Campinas: NEPA-UNICAMP, 2011. 161 p. |
| Licença | "É permitida a reprodução parcial ou total desta obra, desde que citada a fonte" (página de créditos da publicação). No app: `catalog.TACO_LICENSE`, citação por linha (`food_items.citation`) e no rodapé de `/nutricao/alimentos` |
| Arquivo versionado | `docs/nutricao/fontes/taco_4a_edicao.xlsx` (157.519 bytes) |
| SHA-256 do arquivo | `c6f43399897e7ddd5b065764ade3323a0a50354584ff20112e4781f92256d4db` |
| Origem do arquivo | planilha de terceiros (metadados do xlsx: criada no Excel em 2023-05-21 por "Henrique"), entregue pelo usuário em 2026-10-17. **Não é publicação oficial do NEPA**: por isso foi conferida linha a linha contra o PDF oficial (abaixo) |
| Conteúdo | uma aba `Taco`, cabeçalho + 597 alimentos (números 1 a 597), 29 colunas: número, grupo, descrição, umidade, energia (kcal e kJ), proteína, lipídeos, colesterol, carboidrato, fibra alimentar, cinzas, Ca, Mg, Mn, P, Fe, Na, K, Cu, Zn, retinol, RE, RAE, tiamina, riboflavina, piridoxina, niacina, vitamina C |
| Seed gerado | `api/db/migrations/081_food_catalog_seed_taco.py`, pelo gerador `api/scripts/gerar_seed_taco.py` (só biblioteca padrão) |

### Regras de conversão (gerador `api/scripts/gerar_seed_taco.py`)

| Na planilha | No seed | Por quê |
|---|---|---|
| `NA` | NULL | "não aplicável" na legenda da TACO (ex.: proteína e carboidrato dos óleos) |
| `*` | NULL (no seed da 081) | "as análises estão sendo reavaliadas" na legenda (itens 450 e 591 sem nenhum dos 6 campos; 457 e 458 sem energia nem macros; 280, 330, 331, 415, 416 e 417 só sem fibra). Desde o M85 a 084 preenche os 2 leites com o USDA (seção abaixo) |
| célula vazia | NULL | "valores em branco nesta tabela: análises não solicitadas" (ex.: fibra de carnes) |
| `Tr` | 0 | traço: valor arredondado que cai entre 0 e a última casa da publicação, ou abaixo do limite de quantificação |
| número | `Decimal` da representação mais curta do double (o xlsx grava `2.5499999999999998`; `repr` devolve `2.55`, o valor que o Excel mostra), arredondado para 2 casas com ROUND_HALF_UP | precisão cheia da planilha preservada até a 2ª casa |
| negativo acima de −0,05 | 0 | carboidrato calculado por diferença em 4 itens (288, 322, 337, 400: de −0,045 a −0,007 g); a publicação imprime 0,0. Negativo maior é erro do gerador |
| `Energia(kcal)` | `energy_kcal` | a energia vem da coluna de kcal (não convertida do kJ) |
| `Carboidrato(g)` | `carbohydrates_g` | carboidrato TOTAL da TACO (por diferença; inclui a fibra) |
| `Proteína(g)`, `Lipídeos(g)`, `Fibra Alimentar(g)`, `Sódio(mg)` | `protein_g`, `total_fat_g`, `fiber_g`, `sodium_mg` | códigos da RDC 429 do motor (`catalog.PER_100_FIELDS`) |
| — | `total_sugars_g`, `added_sugars_g`, `saturated_fat_g`, `trans_fat_g` = NULL | a planilha não tem essas colunas (a Tabela 2 do PDF tem ácidos graxos saturados e trans; ficam para um lote futuro) |
| descrição | `name` com espaços colapsados e espaço depois da vírgula ("congelado,cozido" → "congelado, cozido") | — |
| "Cana, aguardente 1", "Cerveja, pilsen 2" | "Cana, aguardente", "Cerveja, pilsen" | o "1"/"2" é chamada de nota de rodapé (teor alcoólico) na publicação |
| grupo "Leguminosas e derivados" nos itens 587–597 | "Nozes e sementes" | a planilha junta os dois grupos; a publicação (Tabela 1, p. 63–64) tem 15 grupos, e os 587–597 são "Nozes e sementes" |
| — | `name_normalized` = `food_text.normalize_name(name)` | calculado pelo gerador e embutido; o teste da 081 confere com o motor |
| — | `source = 'taco'`, `source_id` = número, `source_version = '4ed-2011'`, `category` = grupo, `basis = 'g'`, `owner_user_id` NULL (público), `is_active = 1`, `citation`/`license` | — |

Resultado: 597 alimentos em 15 grupos. NULL por campo: energia 6, carboidrato 15,
proteína 15, lipídeos 7, fibra 235, sódio 10. Depois da 084 (M85): energia 4, carboidrato 13,
proteína 13, lipídeos 5, fibra 235, sódio 10.

### Conferência contra o PDF oficial (2026-10-17)

| Item | Valor |
|---|---|
| PDF conferido | `taco_4_edicao_ampliada_e_revisada.pdf`, baixado em 2026-10-17 de https://nepa.unicamp.br/wp-content/uploads/sites/27/2023/10/taco_4_edicao_ampliada_e_revisada.pdf (a página https://nepa.unicamp.br/publicacoes/tabela-taco-pdf/ redireciona para ele) — 744.346 bytes, 164 páginas |
| SHA-256 do PDF | `2002aec5615b5b1395aaa8fa675635bbb7f712c33f278af5e332f1cac8f108c8` |
| Ferramenta | `pdftotext -raw -enc UTF-8` (poppler) + `api/scripts/conferir_taco_pdf.py`. O modo `-layout` desloca colunas inteiras em algumas páginas e gera falsas divergências; o `-raw` mantém cada linha da tabela inteira |
| Regra de comparação | valor da planilha (precisão cheia) arredondado com ROUND_HALF_UP para a precisão impressa (kcal inteiro; g com uma casa; sódio em mg inteiro); `Tr` no PDF exige valor que arredonda para 0; `NA`/`*` exigem NULL |

Resultado — **não é amostra, é a tabela inteira**:

- **Parte centesimal (energia, proteína, lipídeos, carboidrato, fibra)**: 578
  alimentos conferidos automaticamente (linha completa no PDF), **578 batem nos 5
  campos (2.890 de 2.890 valores)**, 0 divergências. Os 19 restantes têm célula
  em branco no PDF (o `-raw` não preserva a coluna vazia) e foram conferidos à
  mão, todos iguais: os 8 óleos e azeites (259, 260, 267–272: 884 kcal, 100 g de
  lipídeos, resto NA), 387, 388, 442, 472, 513, 516, 517, 519, 537, 538 e 548.
  Total: **597 de 597**.
- **Sódio**: 575 conferidos (quando Mn, P e Fe, as colunas antes do sódio,
  batem, garantindo o alinhamento), **575 iguais**, 0 divergências.
- **Grupos**: os 15 cabeçalhos da Tabela 1 começam nos mesmos números do seed
  (1, 64, 163, 259, 273, 323, 446, 470, 484, 491, 511, 520, 525, 557, 587).
- Amostra citada pelo Tech Lead: "Arroz, tipo 1, cozido" (3) = 128 kcal, 2,5 g
  de proteína, 0,2 g de lipídeos, 28,1 g de carboidrato e 1,6 g de fibra no PDF;
  no seed 128,26 / 2,52 / 0,23 / 28,06 / 1,56.

Antes de ler o double pela representação mais curta, 3 valores pareciam
divergir (139 fibra 2,55; 307 proteína 16,65; 337 lipídeos 9,95): o xlsx grava
`2.5499999999999998` e o arredondamento para uma casa dava 2,5 em vez dos 2,6
impressos. Com `repr` os três batem; a mesma regra mudou 0,01 em 18 valores do
seed (meio centésimo exato, agora arredondado para cima como na publicação).

Para refazer:

```
curl -sSLo taco.pdf https://nepa.unicamp.br/wp-content/uploads/sites/27/2023/10/taco_4_edicao_ampliada_e_revisada.pdf
pdftotext -raw -enc UTF-8 taco.pdf taco.txt
cd api && python scripts/conferir_taco_pdf.py ../taco.txt
python scripts/gerar_seed_taco.py --verificar
```

## USDA FoodData Central — `usda_taco_reavaliacao.json` (M85)

Valores dos campos `*` ("em reavaliação") da TACO, por decisão do usuário (2026-10-17): o alimento equivalente do
USDA FoodData Central. O iogurte sabor abacaxi ficou sem valor (decisão de 2026-10-18). Preenchidos pela migração 084
(mapa [`FONTES_DA_VERDADE.md`](../../motores/FONTES_DA_VERDADE.md) §48.1).

| Item | Valor |
|---|---|
| Fonte | U.S. Department of Agriculture, Agricultural Research Service. FoodData Central (`catalog.USDA_CITATION`), tipo SR Legacy (publicação 2019-04-01) |
| Licença | CC0 1.0 Universal (domínio público), `catalog.USDA_LICENSE` |
| Arquivo versionado | `docs/nutricao/fontes/usda_taco_reavaliacao.json`: `retrieved_on` (data da consulta), `operation` e o detalhe COMPLETO de cada alimento, como a API devolveu |
| Consulta | 2026-10-18, `GET https://api.nal.usda.gov/fdc/v1/food/{fdcId}` pelo cliente do L3b (`app.external_food_sources.usda_food`, chave no cabeçalho, nunca no arquivo). Os detalhes são idênticos aos da primeira consulta (2026-10-17) |
| SHA-256 do arquivo (fim de linha LF) | `9b9e30f5a35c67d0e974087c6105c1a8949219f05da31c73fd55fda3b5a57b90` (o mesmo no bloco gerado da 084) |
| Gerador | `api/scripts/gerar_taco_usda_reavaliacao.py`: lê os `*` da planilha pela regra do gerador da 081, mapeia cada detalhe pelo motor do L3b (`external_sources.map_usda_food`: ids de nutriente, unidade, energia, base 100 g), arredonda para 2 casas como a 081 e valida a linha final pelas regras do catálogo |

| Item da TACO | Campos `*` preenchidos | Alimento do USDA (fdcId, tipo) | Valores por 100 g |
|---|---|---|---|
| 457 Leite, de vaca, desnatado, UHT | energia, carboidrato, proteína, gordura | Milk, nonfat, fluid, without added vitamin A and vitamin D (fat free or skim) (173432, SR Legacy) | 35 kcal; 4,86 g; 3,37 g; 0,18 g |
| 458 Leite, de vaca, integral | energia, carboidrato, proteína, gordura | Milk, whole, 3.25% milkfat, without added vitamin A and vitamin D (172217, SR Legacy) | 61 kcal; 4,78 g; 3,15 g; 3,27 g |

Continuam NULL (motivo por item em `SEM_EQUIVALENTE` do gerador): 450 iogurte sabor abacaxi (decisão do usuário: o
iogurte de frutas mais próximo do FDC, "Yogurt, fruit, low fat, 9 g protein/8 oz", fdcId 170889, ~99 kcal, é outro
produto — os iogurtes de sabor da própria TACO têm ~68–70 kcal: morango 451, pêssego 452), 591 coco verde (o FDC só
tem a polpa madura e a água de coco) e a fibra de 280, 330, 331, 415, 416 e 417 (preparações e produtos industriais
sem par no FDC). O sódio dos leites e tudo o que a TACO mediu continuam da TACO; `NA` e vazio continuam NULL.

Para refazer (só antes de a 084 ser aplicada; depois, migração nova):

```
cd api
python scripts/gerar_taco_usda_reavaliacao.py --baixar      # USDA_API_KEY no ambiente
python scripts/gerar_taco_usda_reavaliacao.py --gravar --migracao-nao-aplicada
python scripts/gerar_taco_usda_reavaliacao.py --verificar
```

"""081 - seed da TACO 4ª ed. no catálogo de alimentos (plano de nutrição, Lote L3a, 2026-10-17).

Carrega os 597 alimentos da Tabela Brasileira de Composição de Alimentos – TACO,
4ª ed. rev. e ampl. (NEPA-UNICAMP, 2011) em `food_items` (080), como linhas
PÚBLICAS (`owner_user_id` NULL) e somente leitura pela API
(`domain/nutrition/catalog.READ_ONLY_SOURCES`):

- `source` = 'taco', `source_id` = número do alimento na TACO (1..597),
  `source_version` = `catalog.TACO_SOURCE_VERSION`, `category` = grupo (os 15
  da publicação), `basis` = 'g' (a TACO é por 100 g de parte comestível);
- valores por 100 g: energia (kcal), carboidrato total, proteína, lipídeos,
  fibra e sódio; açúcares e gorduras saturada/trans NULL (a planilha não tem);
- `name_normalized` = `food_text.normalize_name(name)`, calculado pelo gerador
  e EMBUTIDO (o teste `test_migration_081_plano.py` confere com o motor);
- `citation` e `license` em cada linha (risco R4 do plano), iguais às de
  `catalog` (mesmo teste).

Os dados ficam DENTRO deste módulo, gerados por `api/scripts/gerar_seed_taco.py`
a partir de `docs/nutricao/fontes/taco_4a_edicao.xlsx` (SHA-256 no bloco e no
README de fontes): o checksum do runner cobre os dados (decisão D-F; R6). Regras
de conversão no gerador e no README. Migração aplicada não muda: dado novo ou
motor diferente viram migração nova.

--- Fluxo -----------------------------------------------------------------

`plan(db)` só LÊ (`information_schema` + as linhas `source = 'taco'`); `up(db)`
executa. Cada statement é um `INSERT ... ON DUPLICATE KEY UPDATE` (chave
`uq_fi_source (source, source_id)`, 080) de até `BLOCK_SIZE` = 50 linhas
(limite de tamanho de statement da Data API, R6): ⌈597/50⌉ = 12 num banco vazio.

- `food_items` ausente: DRY-RUN planeja os 12 blocos (na execução real a 080
  cria a tabela antes); execução real -> erro;
- coluna escrita ausente ou `uq_fi_source` ausente -> erro antes do 1º statement;
- linha da TACO com `source_id` fora do seed -> erro (migração não apaga dado);
- contagem = 597 e checksum dos dados (`seed_checksum`) igual -> 0 statements
  (reexecução);
- senão só os blocos com linha ausente ou divergente.

`down`/`plan_down`: 1 `DELETE` das linhas `source = 'taco'` (as porções que
apontam para elas são referência lógica e ficam).

Data API: sem PREPARE/SET/multi-statement; um statement por chamada; valores
sempre como parâmetro (nenhuma literal com ':' ou '%' no SQL); módulo
autocontido (não importa código do app).
"""

from __future__ import annotations

import hashlib
import json
import logging
from decimal import Decimal

logger = logging.getLogger("fitness.migrate")

TABLE = "food_items"
SOURCE = "taco"
BASIS = "g"
BLOCK_SIZE = 50
UNIQUE_INDEX = "uq_fi_source"

# >>> DADOS GERADOS por api/scripts/gerar_seed_taco.py (nao editar a mao) >>>
SOURCE_FILE = 'docs/nutricao/fontes/taco_4a_edicao.xlsx'
SOURCE_FILE_SHA256 = 'c6f43399897e7ddd5b065764ade3323a0a50354584ff20112e4781f92256d4db'
SOURCE_VERSION = '4ed-2011'
CITATION = 'Núcleo de Estudos e Pesquisas em Alimentação (NEPA-UNICAMP). Tabela Brasileira de Composição de Alimentos – TACO. 4. ed. rev. e ampl. Campinas: NEPA-UNICAMP, 2011. 161 p.'
LICENSE = 'Reprodução total ou parcial permitida desde que citada a fonte (NEPA-UNICAMP).'
NULL_COLUMNS = ('total_sugars_g', 'added_sugars_g', 'saturated_fat_g', 'trans_fat_g')
SEED_COLUMNS = ('source_id', 'name', 'name_normalized', 'category', 'energy_kcal', 'carbohydrates_g', 'protein_g', 'total_fat_g', 'fiber_g', 'sodium_mg')
SEED_ROWS = (
    ('1', 'Arroz, integral, cozido', 'arroz integral cozido', 'Cereais e derivados', '123.53', '25.81', '2.59', '1.00', '2.75', '1.24'),
    ('2', 'Arroz, integral, cru', 'arroz integral cru', 'Cereais e derivados', '359.68', '77.45', '7.32', '1.86', '4.82', '1.65'),
    ('3', 'Arroz, tipo 1, cozido', 'arroz tipo 1 cozido', 'Cereais e derivados', '128.26', '28.06', '2.52', '0.23', '1.56', '1.20'),
    ('4', 'Arroz, tipo 1, cru', 'arroz tipo 1 cru', 'Cereais e derivados', '357.79', '78.76', '7.16', '0.34', '1.64', '1.02'),
    ('5', 'Arroz, tipo 2, cozido', 'arroz tipo 2 cozido', 'Cereais e derivados', '130.12', '28.19', '2.57', '0.36', '1.07', '1.96'),
    ('6', 'Arroz, tipo 2, cru', 'arroz tipo 2 cru', 'Cereais e derivados', '358.12', '78.88', '7.24', '0.28', '1.72', '0.57'),
    ('7', 'Aveia, flocos, crua', 'aveia flocos cru', 'Cereais e derivados', '393.82', '66.64', '13.92', '8.50', '9.13', '4.63'),
    ('8', 'Biscoito, doce, maisena', 'biscoito doce maisena', 'Cereais e derivados', '442.82', '75.23', '8.07', '11.97', '2.10', '352.03'),
    ('9', 'Biscoito, doce, recheado com chocolate', 'biscoito doce recheado chocolate', 'Cereais e derivados', '471.82', '70.55', '6.40', '19.58', '2.96', '239.20'),
    ('10', 'Biscoito, doce, recheado com morango', 'biscoito doce recheado morango', 'Cereais e derivados', '471.17', '71.01', '5.72', '19.57', '1.53', '229.82'),
    ('11', 'Biscoito, doce, wafer, recheado de chocolate', 'biscoito doce wafer recheado chocolate', 'Cereais e derivados', '502.46', '67.54', '5.56', '24.67', '1.80', '137.24'),
    ('12', 'Biscoito, doce, wafer, recheado de morango', 'biscoito doce wafer recheado morango', 'Cereais e derivados', '513.45', '67.35', '4.52', '26.40', '0.82', '119.90'),
    ('13', 'Biscoito, salgado, cream cracker', 'biscoito salgado cream cracker', 'Cereais e derivados', '431.73', '68.73', '10.06', '14.44', '2.51', '854.36'),
    ('14', 'Bolo, mistura para', 'bolo mistura', 'Cereais e derivados', '418.63', '84.71', '6.16', '6.13', '1.70', '462.88'),
    ('15', 'Bolo, pronto, aipim', 'bolo pronto mandioca', 'Cereais e derivados', '323.85', '47.86', '4.42', '12.75', '0.69', '111.01'),
    ('16', 'Bolo, pronto, chocolate', 'bolo pronto chocolate', 'Cereais e derivados', '410.01', '54.72', '6.22', '18.47', '1.43', '283.30'),
    ('17', 'Bolo, pronto, coco', 'bolo pronto coco', 'Cereais e derivados', '333.44', '52.28', '5.67', '11.30', '1.06', '190.34'),
    ('18', 'Bolo, pronto, milho', 'bolo pronto milho', 'Cereais e derivados', '311.39', '45.11', '4.80', '12.42', '0.71', '133.81'),
    ('19', 'Canjica, branca, crua', 'canjica branca cru', 'Cereais e derivados', '357.60', '78.06', '7.20', '0.97', '5.50', '0.79'),
    ('20', 'Canjica, com leite integral', 'canjica leite integral', 'Cereais e derivados', '112.46', '23.63', '2.36', '1.24', '1.22', '27.59'),
    ('21', 'Cereais, milho, flocos, com sal', 'cereais milho flocos sal', 'Cereais e derivados', '369.60', '80.84', '7.29', '1.60', '5.29', '271.74'),
    ('22', 'Cereais, milho, flocos, sem sal', 'cereais milho flocos sem sal', 'Cereais e derivados', '363.34', '80.45', '6.88', '1.18', '1.84', '30.97'),
    ('23', 'Cereais, mingau, milho, infantil', 'cereais mingau milho infantil', 'Cereais e derivados', '394.43', '87.27', '6.43', '1.09', '3.21', '399.40'),
    ('24', 'Cereais, mistura para vitamina, trigo, cevada e aveia', 'cereais mistura vitamina trigo cevada aveia', 'Cereais e derivados', '381.13', '81.62', '8.90', '2.12', '4.98', '1163.26'),
    ('25', 'Cereal matinal, milho', 'cereal matinal milho', 'Cereais e derivados', '365.35', '83.82', '7.16', '0.96', '4.12', '654.54'),
    ('26', 'Cereal matinal, milho, açúcar', 'cereal matinal milho acucar', 'Cereais e derivados', '376.56', '88.84', '4.74', '0.67', '2.11', '405.31'),
    ('27', 'Creme de arroz, pó', 'creme arroz po', 'Cereais e derivados', '386.00', '83.87', '7.03', '1.23', '1.07', '1.03'),
    ('28', 'Creme de milho, pó', 'creme milho po', 'Cereais e derivados', '333.03', '86.15', '4.82', '1.64', '3.72', '593.79'),
    ('29', 'Curau, milho verde', 'curau milho verde', 'Cereais e derivados', '78.43', '13.94', '2.36', '1.64', '0.46', '20.51'),
    ('30', 'Curau, milho verde, mistura para', 'curau milho verde mistura', 'Cereais e derivados', '402.29', '79.82', '2.22', '13.37', '2.52', '222.93'),
    ('31', 'Farinha, de arroz, enriquecida', 'farinha arroz enriquecida', 'Cereais e derivados', '363.06', '85.50', '1.27', '0.30', '0.58', '17.10'),
    ('32', 'Farinha, de centeio, integral', 'farinha centeio integral', 'Cereais e derivados', '335.78', '73.30', '12.52', '1.75', '15.48', '41.38'),
    ('33', 'Farinha, de milho, amarela', 'farinha milho amarela', 'Cereais e derivados', '350.59', '79.08', '7.19', '1.47', '5.49', '44.93'),
    ('34', 'Farinha, de rosca', 'farinha rosca', 'Cereais e derivados', '370.58', '75.79', '11.38', '1.46', '4.82', '332.50'),
    ('35', 'Farinha, de trigo', 'farinha trigo', 'Cereais e derivados', '360.47', '75.09', '9.79', '1.37', '2.35', '0.74'),
    ('36', 'Farinha, láctea, de cereais', 'farinha lactea cereais', 'Cereais e derivados', '414.85', '77.77', '11.88', '5.79', '1.94', '125.07'),
    ('37', 'Lasanha, massa fresca, cozida', 'lasanha massa fresca cozido', 'Cereais e derivados', '163.76', '32.52', '5.81', '1.16', '1.64', '206.77'),
    ('38', 'Lasanha, massa fresca, crua', 'lasanha massa fresca cru', 'Cereais e derivados', '220.31', '45.06', '7.01', '1.34', '1.61', '666.71'),
    ('39', 'Macarrão, instantâneo', 'macarrao instantaneo', 'Cereais e derivados', '435.86', '62.43', '8.79', '17.24', '5.61', '1515.53'),
    ('40', 'Macarrão, trigo, cru', 'macarrao trigo cru', 'Cereais e derivados', '371.12', '77.94', '10.00', '1.30', '2.93', '7.17'),
    ('41', 'Macarrão, trigo, cru, com ovos', 'macarrao trigo cru ovo', 'Cereais e derivados', '370.57', '76.62', '10.32', '1.97', '2.30', '14.74'),
    ('42', 'Milho, amido, cru', 'milho amido cru', 'Cereais e derivados', '361.37', '87.15', '0.60', '0.00', '0.74', '8.08'),
    ('43', 'Milho, fubá, cru', 'milho fuba cru', 'Cereais e derivados', '353.48', '78.87', '7.21', '1.90', '4.71', '0.00'),
    ('44', 'Milho, verde, cru', 'milho verde cru', 'Cereais e derivados', '138.17', '28.56', '6.59', '0.61', '3.92', '1.12'),
    ('45', 'Milho, verde, enlatado, drenado', 'milho verde enlatado drenado', 'Cereais e derivados', '97.56', '17.14', '3.23', '2.35', '4.64', '260.35'),
    ('46', 'Mingau tradicional, pó', 'mingau tradicional po', 'Cereais e derivados', '373.42', '89.34', '0.58', '0.37', '0.88', '14.86'),
    ('47', 'Pamonha, barra para cozimento, pré-cozida', 'pamonha barra cozimento pre cozido', 'Cereais e derivados', '171.22', '30.68', '2.55', '4.85', '2.37', '131.99'),
    ('48', 'Pão, aveia, forma', 'pao aveia forma', 'Cereais e derivados', '343.09', '59.57', '12.35', '5.69', '5.98', '605.76'),
    ('49', 'Pão, de soja', 'pao soja', 'Cereais e derivados', '308.73', '56.51', '11.34', '3.58', '5.71', '662.54'),
    ('50', 'Pão, glúten, forma', 'pao gluten forma', 'Cereais e derivados', '252.99', '44.12', '11.95', '2.73', '2.48', '22.05'),
    ('51', 'Pão, milho, forma', 'pao milho forma', 'Cereais e derivados', '292.01', '56.40', '8.30', '3.11', '4.30', '506.64'),
    ('52', 'Pão, trigo, forma, integral', 'pao forma integral', 'Cereais e derivados', '253.19', '49.94', '9.43', '3.65', '6.88', '506.10'),
    ('53', 'Pão, trigo, francês', 'pao frances', 'Cereais e derivados', '299.81', '58.65', '7.95', '3.10', '2.31', '647.67'),
    ('54', 'Pão, trigo, sovado', 'pao sovado', 'Cereais e derivados', '310.96', '61.45', '8.40', '2.84', '2.43', '430.79'),
    ('55', 'Pastel, de carne, cru', 'pastel carne cru', 'Cereais e derivados', '288.70', '42.02', '10.74', '8.79', '1.04', '1309.27'),
    ('56', 'Pastel, de carne, frito', 'pastel carne frito', 'Cereais e derivados', '388.37', '43.77', '10.10', '20.14', '0.99', '1039.89'),
    ('57', 'Pastel, de queijo, cru', 'pastel queijo cru', 'Cereais e derivados', '308.47', '45.95', '9.85', '9.63', '1.11', '984.57'),
    ('58', 'Pastel, de queijo, frito', 'pastel queijo frito', 'Cereais e derivados', '422.11', '48.13', '8.71', '22.67', '0.94', '821.38'),
    ('59', 'Pastel, massa, crua', 'pastel massa cru', 'Cereais e derivados', '310.20', '57.38', '6.90', '5.48', '1.41', '1344.20'),
    ('60', 'Pastel, massa, frita', 'pastel massa frito', 'Cereais e derivados', '569.67', '49.34', '6.02', '40.86', '1.31', '1174.67'),
    ('61', 'Pipoca, com óleo de soja, sem sal', 'pipoca oleo soja sem sal', 'Cereais e derivados', '448.33', '70.31', '9.93', '15.94', '14.34', '4.32'),
    ('62', 'Polenta, pré-cozida', 'polenta pre cozido', 'Cereais e derivados', '102.74', '23.31', '2.29', '0.30', '2.40', '441.89'),
    ('63', 'Torrada, pão francês', 'torrada pao frances', 'Cereais e derivados', '377.42', '74.56', '10.52', '3.30', '3.40', '829.49'),
    ('64', 'Abóbora, cabotian, cozida', 'abobora cabotian cozido', 'Verduras, hortaliças e derivados', '48.04', '10.76', '1.44', '0.73', '2.46', '1.45'),
    ('65', 'Abóbora, cabotian, crua', 'abobora cabotian cru', 'Verduras, hortaliças e derivados', '38.60', '8.36', '1.75', '0.54', '2.17', '0.00'),
    ('66', 'Abóbora, menina brasileira, crua', 'abobora menina brasileira cru', 'Verduras, hortaliças e derivados', '13.61', '3.30', '0.61', '0.00', '1.17', '0.00'),
    ('67', 'Abóbora, moranga, crua', 'abobora moranga cru', 'Verduras, hortaliças e derivados', '12.36', '2.67', '0.96', '0.06', '1.70', '0.00'),
    ('68', 'Abóbora, moranga, refogada', 'abobora moranga refogado', 'Verduras, hortaliças e derivados', '29.00', '5.98', '0.39', '0.80', '1.55', '3.03'),
    ('69', 'Abóbora, pescoço, crua', 'abobora pescoco cru', 'Verduras, hortaliças e derivados', '24.47', '6.12', '0.67', '0.12', '2.30', '0.75'),
    ('70', 'Abobrinha, italiana, cozida', 'abobrinha italiana cozido', 'Verduras, hortaliças e derivados', '15.04', '2.98', '1.13', '0.20', '1.59', '0.83'),
    ('71', 'Abobrinha, italiana, crua', 'abobrinha italiana cru', 'Verduras, hortaliças e derivados', '19.28', '4.29', '1.14', '0.14', '1.35', '0.00'),
    ('72', 'Abobrinha, italiana, refogada', 'abobrinha italiana refogado', 'Verduras, hortaliças e derivados', '24.43', '4.19', '1.07', '0.82', '1.38', '2.21'),
    ('73', 'Abobrinha, paulista, crua', 'abobrinha paulista cru', 'Verduras, hortaliças e derivados', '30.81', '7.87', '0.64', '0.14', '2.61', '0.50'),
    ('74', 'Acelga, crua', 'acelga cru', 'Verduras, hortaliças e derivados', '20.94', '4.63', '1.44', '0.11', '1.12', '1.18'),
    ('75', 'Agrião, cru', 'agriao cru', 'Verduras, hortaliças e derivados', '16.58', '2.25', '2.69', '0.24', '2.14', '7.46'),
    ('76', 'Aipo, cru', 'aipo cru', 'Verduras, hortaliças e derivados', '19.09', '4.27', '0.76', '0.07', '0.96', '9.52'),
    ('77', 'Alface, americana, crua', 'alface americana cru', 'Verduras, hortaliças e derivados', '8.79', '1.75', '0.61', '0.13', '1.02', '7.31'),
    ('78', 'Alface, crespa, crua', 'alface crespa cru', 'Verduras, hortaliças e derivados', '10.68', '1.70', '1.35', '0.16', '1.83', '3.38'),
    ('79', 'Alface, lisa, crua', 'alface lisa cru', 'Verduras, hortaliças e derivados', '13.82', '2.43', '1.69', '0.12', '2.33', '4.23'),
    ('80', 'Alface, roxa, crua', 'alface roxa cru', 'Verduras, hortaliças e derivados', '12.72', '2.49', '0.91', '0.19', '2.01', '7.12'),
    ('81', 'Alfavaca, crua', 'alfavaca cru', 'Verduras, hortaliças e derivados', '29.18', '5.24', '2.66', '0.48', '4.14', '4.55'),
    ('82', 'Alho, cru', 'alho cru', 'Verduras, hortaliças e derivados', '113.13', '23.91', '7.01', '0.22', '4.32', '5.36'),
    ('83', 'Alho-poró, cru', 'alho poro cru', 'Verduras, hortaliças e derivados', '31.51', '6.88', '1.41', '0.14', '2.51', '1.76'),
    ('84', 'Almeirão, cru', 'almeirao cru', 'Verduras, hortaliças e derivados', '18.03', '3.34', '1.77', '0.22', '2.59', '2.35'),
    ('85', 'Almeirão, refogado', 'almeirao refogado', 'Verduras, hortaliças e derivados', '65.08', '5.70', '1.70', '4.85', '3.43', '14.52'),
    ('86', 'Batata, baroa, cozida', 'batata baroa cozido', 'Verduras, hortaliças e derivados', '80.12', '18.95', '0.85', '0.17', '1.76', '2.10'),
    ('87', 'Batata, baroa, crua', 'batata baroa cru', 'Verduras, hortaliças e derivados', '100.98', '23.98', '1.05', '0.17', '2.06', '0.00'),
    ('88', 'Batata, doce, cozida', 'batata doce cozido', 'Verduras, hortaliças e derivados', '76.76', '18.42', '0.64', '0.09', '2.21', '2.70'),
    ('89', 'Batata, doce, crua', 'batata doce cru', 'Verduras, hortaliças e derivados', '118.24', '28.20', '1.26', '0.13', '2.57', '8.77'),
    ('90', 'Batata, frita, tipo chips, industrializada', 'batata frito tipo chips industrializada', 'Verduras, hortaliças e derivados', '542.73', '51.22', '5.58', '36.62', '2.46', '607.40'),
    ('91', 'Batata, inglesa, cozida', 'batata inglesa cozido', 'Verduras, hortaliças e derivados', '51.59', '11.94', '1.16', '0.00', '1.34', '2.29'),
    ('92', 'Batata, inglesa, crua', 'batata inglesa cru', 'Verduras, hortaliças e derivados', '64.37', '14.69', '1.77', '0.00', '1.16', '0.00'),
    ('93', 'Batata, inglesa, frita', 'batata inglesa frito', 'Verduras, hortaliças e derivados', '267.16', '35.64', '4.97', '13.11', '8.06', '1.91'),
    ('94', 'Batata, inglesa, sauté', 'batata inglesa saute', 'Verduras, hortaliças e derivados', '67.89', '14.09', '1.29', '0.90', '1.38', '8.18'),
    ('95', 'Berinjela, cozida', 'berinjela cozido', 'Verduras, hortaliças e derivados', '18.85', '4.47', '0.68', '0.15', '2.52', '1.33'),
    ('96', 'Berinjela, crua', 'berinjela cru', 'Verduras, hortaliças e derivados', '19.63', '4.43', '1.22', '0.10', '2.87', '0.00'),
    ('97', 'Beterraba, cozida', 'beterraba cozido', 'Verduras, hortaliças e derivados', '32.15', '7.23', '1.29', '0.09', '1.88', '22.76'),
    ('98', 'Beterraba, crua', 'beterraba cru', 'Verduras, hortaliças e derivados', '48.83', '11.11', '1.95', '0.09', '3.37', '9.72'),
    ('99', 'Biscoito, polvilho doce', 'biscoito polvilho doce', 'Verduras, hortaliças e derivados', '437.55', '80.54', '1.29', '12.25', '1.16', '97.80'),
    ('100', 'Brócolis, cozido', 'brocolis cozido', 'Verduras, hortaliças e derivados', '24.64', '4.37', '2.13', '0.46', '3.42', '2.12'),
    ('101', 'Brócolis, cru', 'brocolis cru', 'Verduras, hortaliças e derivados', '25.50', '4.03', '3.64', '0.27', '2.88', '3.33'),
    ('102', 'Cará, cozido', 'inhame cozido', 'Verduras, hortaliças e derivados', '77.58', '18.85', '1.53', '0.11', '2.63', '1.01'),
    ('103', 'Cará, cru', 'inhame cru', 'Verduras, hortaliças e derivados', '95.63', '22.95', '2.28', '0.14', '7.27', '0.00'),
    ('104', 'Caruru, cru', 'caruru cru', 'Verduras, hortaliças e derivados', '34.03', '5.97', '3.20', '0.59', '4.47', '13.67'),
    ('105', 'Catalonha, crua', 'catalonha cru', 'Verduras, hortaliças e derivados', '23.89', '4.75', '1.87', '0.28', '2.05', '9.39'),
    ('106', 'Catalonha, refogada', 'catalonha refogado', 'Verduras, hortaliças e derivados', '63.45', '4.81', '1.95', '4.81', '3.65', '24.72'),
    ('107', 'Cebola, crua', 'cebola cru', 'Verduras, hortaliças e derivados', '39.42', '8.85', '1.71', '0.08', '2.19', '0.60'),
    ('108', 'Cebolinha, crua', 'cebolinha cru', 'Verduras, hortaliças e derivados', '19.52', '3.37', '1.87', '0.35', '3.55', '1.60'),
    ('109', 'Cenoura, cozida', 'cenoura cozido', 'Verduras, hortaliças e derivados', '29.86', '6.69', '0.85', '0.22', '2.63', '7.88'),
    ('110', 'Cenoura, crua', 'cenoura cru', 'Verduras, hortaliças e derivados', '34.14', '7.66', '1.32', '0.17', '3.18', '3.33'),
    ('111', 'Chicória, crua', 'chicoria cru', 'Verduras, hortaliças e derivados', '13.84', '2.85', '1.14', '0.14', '2.20', '13.52'),
    ('112', 'Chuchu, cozido', 'chuchu cozido', 'Verduras, hortaliças e derivados', '18.54', '4.79', '0.41', '0.00', '1.04', '1.81'),
    ('113', 'Chuchu, cru', 'chuchu cru', 'Verduras, hortaliças e derivados', '16.98', '4.14', '0.70', '0.06', '1.28', '0.00'),
    ('114', 'Coentro, folhas desidratadas', 'coentro folhas desidratadas', 'Verduras, hortaliças e derivados', '309.07', '47.96', '20.88', '10.39', '37.29', '18.26'),
    ('115', 'Couve, manteiga, crua', 'couve cru', 'Verduras, hortaliças e derivados', '27.06', '4.33', '2.87', '0.55', '3.12', '6.17'),
    ('116', 'Couve, manteiga, refogada', 'couve refogado', 'Verduras, hortaliças e derivados', '90.34', '8.71', '1.67', '6.59', '5.74', '11.45'),
    ('117', 'Couve-flor, crua', 'couve flor cru', 'Verduras, hortaliças e derivados', '22.56', '4.52', '1.91', '0.21', '2.35', '3.44'),
    ('118', 'Couve-flor, cozida', 'couve flor cozido', 'Verduras, hortaliças e derivados', '19.11', '3.88', '1.24', '0.27', '2.13', '1.79'),
    ('119', 'Espinafre, Nova Zelândia, cru', 'espinafre nova zelandia cru', 'Verduras, hortaliças e derivados', '16.10', '2.57', '2.00', '0.24', '2.10', '17.09'),
    ('120', 'Espinafre, Nova Zelândia, refogado', 'espinafre nova zelandia refogado', 'Verduras, hortaliças e derivados', '67.25', '4.24', '2.72', '5.43', '2.52', '47.02'),
    ('121', 'Farinha, de mandioca, crua', 'farinha mandioca cru', 'Verduras, hortaliças e derivados', '360.87', '87.90', '1.55', '0.28', '6.39', '1.02'),
    ('122', 'Farinha, de mandioca, torrada', 'farinha mandioca torrada', 'Verduras, hortaliças e derivados', '365.27', '89.19', '1.23', '0.29', '6.54', '10.31'),
    ('123', 'Farinha, de puba', 'farinha puba', 'Verduras, hortaliças e derivados', '360.18', '87.29', '1.62', '0.47', '4.24', '3.61'),
    ('124', 'Fécula, de mandioca', 'fecula mandioca', 'Verduras, hortaliças e derivados', '330.85', '81.15', '0.52', '0.28', '0.65', '2.45'),
    ('125', 'Feijão, broto, cru', 'feijao broto cru', 'Verduras, hortaliças e derivados', '38.72', '7.76', '4.17', '0.10', '1.97', '1.79'),
    ('126', 'Inhame, cru', 'inhame cru', 'Verduras, hortaliças e derivados', '96.70', '23.23', '2.05', '0.21', '1.65', '0.00'),
    ('127', 'Jiló, cru', 'jilo cru', 'Verduras, hortaliças e derivados', '27.37', '6.19', '1.40', '0.22', '4.83', '0.00'),
    ('128', 'Jurubeba, crua', 'jurubeba cru', 'Verduras, hortaliças e derivados', '125.81', '23.06', '4.41', '3.91', '23.92', '0.77'),
    ('129', 'Mandioca, cozida', 'mandioca cozido', 'Verduras, hortaliças e derivados', '125.36', '30.09', '0.58', '0.30', '1.56', '0.91'),
    ('130', 'Mandioca, crua', 'mandioca cru', 'Verduras, hortaliças e derivados', '151.42', '36.17', '1.13', '0.30', '1.88', '2.15'),
    ('131', 'Mandioca, farofa, temperada', 'mandioca farofa temperada', 'Verduras, hortaliças e derivados', '405.69', '80.30', '2.06', '9.12', '7.82', '574.51'),
    ('132', 'Mandioca, frita', 'mandioca frito', 'Verduras, hortaliças e derivados', '300.06', '50.25', '1.38', '11.20', '1.87', '8.94'),
    ('133', 'Manjericão, cru', 'manjericao cru', 'Verduras, hortaliças e derivados', '21.15', '3.64', '1.99', '0.39', '3.31', '3.89'),
    ('134', 'Maxixe, cru', 'maxixe cru', 'Verduras, hortaliças e derivados', '13.75', '2.73', '1.39', '0.07', '2.19', '10.99'),
    ('135', 'Mostarda, folha, crua', 'mostarda folha cru', 'Verduras, hortaliças e derivados', '18.11', '3.24', '2.11', '0.17', '1.89', '2.88'),
    ('136', 'Nhoque, batata, cozido', 'nhoque batata cozido', 'Verduras, hortaliças e derivados', '180.78', '36.78', '5.86', '1.94', '1.78', '7.07'),
    ('137', 'Nabo, cru', 'nabo cru', 'Verduras, hortaliças e derivados', '18.19', '4.15', '1.20', '0.05', '2.64', '2.46'),
    ('138', 'Palmito, juçara, em conserva', 'palmito jucara conserva', 'Verduras, hortaliças e derivados', '23.20', '4.33', '1.79', '0.40', '3.15', '513.82'),
    ('139', 'Palmito, pupunha, em conserva', 'palmito pupunha conserva', 'Verduras, hortaliças e derivados', '29.43', '5.51', '2.46', '0.45', '2.55', '562.69'),
    ('140', 'Pão, de queijo, assado', 'pao queijo assado', 'Verduras, hortaliças e derivados', '363.08', '34.24', '5.12', '24.57', '0.56', '773.49'),
    ('141', 'Pão, de queijo, cru', 'pao queijo cru', 'Verduras, hortaliças e derivados', '294.54', '38.51', '3.65', '13.99', '0.98', '404.99'),
    ('142', 'Pepino, cru', 'pepino cru', 'Verduras, hortaliças e derivados', '9.53', '2.04', '0.87', '0.00', '1.12', '0.00'),
    ('143', 'Pimentão, amarelo, cru', 'pimentao amarelo cru', 'Verduras, hortaliças e derivados', '27.93', '5.96', '1.22', '0.44', '1.92', '0.00'),
    ('144', 'Pimentão, verde, cru', 'pimentao verde cru', 'Verduras, hortaliças e derivados', '21.29', '4.89', '1.05', '0.15', '2.56', '0.00'),
    ('145', 'Pimentão, vermelho, cru', 'pimentao vermelho cru', 'Verduras, hortaliças e derivados', '23.28', '5.47', '1.04', '0.15', '1.59', '0.00'),
    ('146', 'Polvilho, doce', 'polvilho doce', 'Verduras, hortaliças e derivados', '351.23', '86.77', '0.43', '0.00', '0.24', '1.58'),
    ('147', 'Quiabo, cru', 'quiabo cru', 'Verduras, hortaliças e derivados', '29.94', '6.37', '1.92', '0.30', '4.55', '0.89'),
    ('148', 'Rabanete, cru', 'rabanete cru', 'Verduras, hortaliças e derivados', '13.74', '2.73', '1.39', '0.07', '2.19', '10.99'),
    ('149', 'Repolho, branco, cru', 'repolho branco cru', 'Verduras, hortaliças e derivados', '17.12', '3.86', '0.88', '0.14', '1.89', '3.64'),
    ('150', 'Repolho, roxo, cru', 'repolho roxo cru', 'Verduras, hortaliças e derivados', '30.91', '7.20', '1.91', '0.06', '1.97', '2.34'),
    ('151', 'Repolho, roxo, refogado', 'repolho roxo refogado', 'Verduras, hortaliças e derivados', '41.77', '7.56', '1.80', '1.24', '1.75', '3.42'),
    ('152', 'Rúcula, crua', 'rucula cru', 'Verduras, hortaliças e derivados', '13.13', '2.22', '1.77', '0.11', '1.74', '9.42'),
    ('153', 'Salsa, crua', 'salsa cru', 'Verduras, hortaliças e derivados', '33.42', '5.71', '3.26', '0.61', '1.85', '2.30'),
    ('154', 'Seleta de legumes, enlatada', 'seleta legumes enlatada', 'Verduras, hortaliças e derivados', '56.53', '12.67', '3.42', '0.35', '3.09', '398.14'),
    ('155', 'Serralha, crua', 'serralha cru', 'Verduras, hortaliças e derivados', '30.40', '4.95', '2.67', '0.74', '3.52', '19.35'),
    ('156', 'Taioba, crua', 'taioba cru', 'Verduras, hortaliças e derivados', '34.21', '5.43', '2.90', '0.93', '4.45', '1.16'),
    ('157', 'Tomate, com semente, cru', 'tomate semente cru', 'Verduras, hortaliças e derivados', '15.34', '3.14', '1.10', '0.17', '1.17', '1.02'),
    ('158', 'Tomate, extrato', 'tomate extrato', 'Verduras, hortaliças e derivados', '60.93', '14.96', '2.43', '0.19', '2.80', '497.93'),
    ('159', 'Tomate, molho industrializado', 'tomate molho industrializado', 'Verduras, hortaliças e derivados', '38.45', '7.71', '1.38', '0.90', '3.12', '418.28'),
    ('160', 'Tomate, purê', 'tomate pure', 'Verduras, hortaliças e derivados', '27.94', '6.89', '1.36', '0.00', '1.03', '103.93'),
    ('161', 'Tomate, salada', 'tomate salada', 'Verduras, hortaliças e derivados', '20.55', '5.12', '0.81', '0.00', '2.27', '5.24'),
    ('162', 'Vagem, crua', 'vagem cru', 'Verduras, hortaliças e derivados', '24.90', '5.35', '1.79', '0.17', '2.38', '0.00'),
    ('163', 'Abacate, cru', 'abacate cru', 'Frutas e derivados', '96.15', '6.03', '1.24', '8.40', '6.31', '0.00'),
    ('164', 'Abacaxi, cru', 'abacaxi cru', 'Frutas e derivados', '48.32', '12.33', '0.86', '0.12', '0.99', '0.00'),
    ('165', 'Abacaxi, polpa, congelada', 'abacaxi polpa congelada', 'Frutas e derivados', '30.59', '7.80', '0.47', '0.11', '0.33', '1.24'),
    ('166', 'Abiu, cru', 'abiu cru', 'Frutas e derivados', '62.42', '14.93', '0.83', '0.70', '1.70', '0.00'),
    ('167', 'Açaí, polpa, com xarope de guaraná e glucose', 'acai polpa xarope guarana glucose', 'Frutas e derivados', '110.30', '21.46', '0.72', '3.66', '1.72', '15.10'),
    ('168', 'Açaí, polpa, congelada', 'acai polpa congelada', 'Frutas e derivados', '58.05', '6.21', '0.80', '3.94', '2.55', '5.18'),
    ('169', 'Acerola, crua', 'acerola cru', 'Frutas e derivados', '33.46', '7.97', '0.91', '0.21', '1.51', '0.00'),
    ('170', 'Acerola, polpa, congelada', 'acerola polpa congelada', 'Frutas e derivados', '21.94', '5.54', '0.59', '0.00', '0.70', '1.28'),
    ('171', 'Ameixa, calda, enlatada', 'ameixa calda enlatada', 'Frutas e derivados', '182.85', '46.89', '0.41', '0.00', '0.52', '2.70'),
    ('172', 'Ameixa, crua', 'ameixa cru', 'Frutas e derivados', '52.54', '13.85', '0.77', '0.00', '2.43', '0.00'),
    ('173', 'Ameixa, em calda, enlatada, drenada', 'ameixa calda enlatada drenada', 'Frutas e derivados', '177.36', '47.66', '1.03', '0.28', '4.55', '2.79'),
    ('174', 'Atemóia, crua', 'atemoia cru', 'Frutas e derivados', '96.97', '25.33', '0.97', '0.30', '2.14', '0.79'),
    ('175', 'Banana, da terra, crua', 'banana terra cru', 'Frutas e derivados', '128.02', '33.67', '1.43', '0.24', '1.53', '0.00'),
    ('176', 'Banana, doce em barra', 'banana doce barra', 'Frutas e derivados', '280.11', '75.67', '2.17', '0.05', '3.83', '9.88'),
    ('177', 'Banana, figo, crua', 'banana figo cru', 'Frutas e derivados', '105.08', '27.80', '1.13', '0.14', '2.80', '0.00'),
    ('178', 'Banana, maçã, crua', 'banana maca cru', 'Frutas e derivados', '86.81', '22.34', '1.75', '0.06', '2.59', '0.00'),
    ('179', 'Banana, nanica, crua', 'banana nanica cru', 'Frutas e derivados', '91.53', '23.85', '1.40', '0.12', '1.95', '0.00'),
    ('180', 'Banana, ouro, crua', 'banana ouro cru', 'Frutas e derivados', '112.37', '29.34', '1.48', '0.21', '1.95', '0.00'),
    ('181', 'Banana, pacova, crua', 'banana pacova cru', 'Frutas e derivados', '77.91', '20.31', '1.23', '0.08', '2.03', '0.94'),
    ('182', 'Banana, prata, crua', 'banana prata cru', 'Frutas e derivados', '98.25', '25.96', '1.27', '0.07', '2.04', '0.00'),
    ('183', 'Cacau, cru', 'cacau cru', 'Frutas e derivados', '74.29', '19.41', '0.95', '0.14', '2.19', '0.70'),
    ('184', 'Cajá-Manga, cru', 'caja manga cru', 'Frutas e derivados', '45.58', '11.43', '1.28', '0.00', '2.58', '1.44'),
    ('185', 'Cajá, polpa, congelada', 'caja polpa congelada', 'Frutas e derivados', '26.33', '6.37', '0.59', '0.17', '1.36', '6.95'),
    ('186', 'Caju, cru', 'caju cru', 'Frutas e derivados', '43.07', '10.29', '0.97', '0.33', '1.68', '2.97'),
    ('187', 'Caju, polpa, congelada', 'caju polpa congelada', 'Frutas e derivados', '36.57', '9.35', '0.48', '0.15', '0.81', '4.16'),
    ('188', 'Caju, suco concentrado, envasado', 'caju suco concentrado envasado', 'Frutas e derivados', '45.11', '10.73', '0.40', '0.20', '0.63', '45.04'),
    ('189', 'Caqui, chocolate, cru', 'caqui chocolate cru', 'Frutas e derivados', '71.35', '19.33', '0.36', '0.07', '6.52', '2.18'),
    ('190', 'Carambola, crua', 'carambola cru', 'Frutas e derivados', '45.74', '11.48', '0.87', '0.18', '2.03', '4.09'),
    ('191', 'Ciriguela, crua', 'ciriguela cru', 'Frutas e derivados', '75.59', '18.86', '1.40', '0.36', '3.90', '1.68'),
    ('192', 'Cupuaçu, cru', 'cupuacu cru', 'Frutas e derivados', '49.42', '10.43', '1.16', '0.95', '3.12', '3.20'),
    ('193', 'Cupuaçu, polpa, congelada', 'cupuacu polpa congelada', 'Frutas e derivados', '48.80', '11.39', '0.84', '0.59', '1.59', '0.69'),
    ('194', 'Figo, cru', 'figo cru', 'Frutas e derivados', '41.45', '10.25', '0.97', '0.16', '1.79', '0.00'),
    ('195', 'Figo, enlatado, em calda', 'figo enlatado calda', 'Frutas e derivados', '184.36', '50.34', '0.56', '0.15', '1.98', '6.87'),
    ('196', 'Fruta-pão, crua', 'fruta pao cru', 'Frutas e derivados', '67.05', '17.17', '1.08', '0.19', '5.55', '0.80'),
    ('197', 'Goiaba, branca, com casca, crua', 'goiaba branca casca cru', 'Frutas e derivados', '51.74', '12.40', '0.90', '0.49', '6.33', '0.00'),
    ('198', 'Goiaba, doce em pasta', 'goiaba doce pasta', 'Frutas e derivados', '268.96', '74.12', '0.58', '0.00', '3.73', '3.70'),
    ('199', 'Goiaba, doce, cascão', 'goiaba doce cascao', 'Frutas e derivados', '285.59', '78.70', '0.41', '0.10', '4.37', '11.03'),
    ('200', 'Goiaba, vermelha, com casca, crua', 'goiaba vermelha casca cru', 'Frutas e derivados', '54.17', '13.01', '1.09', '0.44', '6.22', '0.00'),
    ('201', 'Graviola, crua', 'graviola cru', 'Frutas e derivados', '61.62', '15.84', '0.85', '0.21', '1.91', '4.16'),
    ('202', 'Graviola, polpa, congelada', 'graviola polpa congelada', 'Frutas e derivados', '38.27', '9.78', '0.57', '0.14', '1.19', '3.05'),
    ('203', 'Jabuticaba, crua', 'jabuticaba cru', 'Frutas e derivados', '58.05', '15.26', '0.61', '0.13', '2.30', '0.00'),
    ('204', 'Jaca, crua', 'jaca cru', 'Frutas e derivados', '87.92', '22.50', '1.40', '0.27', '2.39', '1.80'),
    ('205', 'Jambo, cru', 'jambo cru', 'Frutas e derivados', '26.91', '6.49', '0.89', '0.07', '5.07', '21.66'),
    ('206', 'Jamelão, cru', 'jamelao cru', 'Frutas e derivados', '41.01', '10.63', '0.55', '0.11', '1.78', '1.37'),
    ('207', 'Kiwi, cru', 'kiwi cru', 'Frutas e derivados', '51.14', '11.50', '1.34', '0.63', '2.65', '0.00'),
    ('208', 'Laranja, baía, crua', 'laranja baia cru', 'Frutas e derivados', '45.44', '11.47', '0.98', '0.10', '1.12', '0.00'),
    ('209', 'Laranja, baía, suco', 'laranja baia suco', 'Frutas e derivados', '36.65', '8.70', '0.65', '0.00', '0.00', '0.00'),
    ('210', 'Laranja, da terra, crua', 'laranja terra cru', 'Frutas e derivados', '51.47', '12.86', '1.08', '0.19', '3.98', '0.83'),
    ('211', 'Laranja, da terra, suco', 'laranja terra suco', 'Frutas e derivados', '40.96', '9.57', '0.67', '0.14', '1.03', '0.00'),
    ('212', 'Laranja, lima, crua', 'laranja lima cru', 'Frutas e derivados', '45.70', '11.53', '1.06', '0.08', '1.78', '1.11'),
    ('213', 'Laranja, lima, suco', 'laranja lima suco', 'Frutas e derivados', '39.34', '9.17', '0.71', '0.12', '0.42', '0.00'),
    ('214', 'Laranja, pêra, crua', 'laranja pera cru', 'Frutas e derivados', '36.77', '8.95', '1.04', '0.13', '0.77', '0.00'),
    ('215', 'Laranja, pêra, suco', 'laranja pera suco', 'Frutas e derivados', '32.71', '7.55', '0.74', '0.07', '0.00', '0.00'),
    ('216', 'Laranja, valência, crua', 'laranja valencia cru', 'Frutas e derivados', '46.11', '11.72', '0.77', '0.16', '1.73', '0.63'),
    ('217', 'Laranja, valência, suco', 'laranja valencia suco', 'Frutas e derivados', '36.20', '8.55', '0.48', '0.12', '0.42', '0.00'),
    ('218', 'Limão, cravo, suco', 'limao cravo suco', 'Frutas e derivados', '14.10', '5.25', '0.33', '0.00', '0.00', '0.00'),
    ('219', 'Limão, galego, suco', 'limao galego suco', 'Frutas e derivados', '22.23', '7.32', '0.57', '0.07', '0.00', '0.00'),
    ('220', 'Limão, tahiti, cru', 'limao tahiti cru', 'Frutas e derivados', '31.82', '11.08', '0.94', '0.14', '1.18', '1.25'),
    ('221', 'Maçã, Argentina, com casca, crua', 'maca argentina casca cru', 'Frutas e derivados', '62.53', '16.59', '0.23', '0.25', '2.03', '1.32'),
    ('222', 'Maçã, Fuji, com casca, crua', 'maca fuji casca cru', 'Frutas e derivados', '55.52', '15.15', '0.29', '0.00', '1.35', '0.00'),
    ('223', 'Macaúba, crua', 'macauba cru', 'Frutas e derivados', '404.28', '13.95', '2.08', '40.66', '13.44', '0.65'),
    ('224', 'Mamão, doce em calda, drenado', 'mamao doce calda drenado', 'Frutas e derivados', '195.63', '54.00', '0.19', '0.07', '1.31', '2.91'),
    ('225', 'Mamão, Formosa, cru', 'mamao formosa cru', 'Frutas e derivados', '45.34', '11.55', '0.82', '0.12', '1.81', '3.26'),
    ('226', 'Mamão, Papaia, cru', 'mamao papaia cru', 'Frutas e derivados', '40.16', '10.44', '0.46', '0.12', '1.04', '1.63'),
    ('227', 'Mamão verde, doce em calda, drenado', 'mamao verde doce calda drenado', 'Frutas e derivados', '209.38', '57.64', '0.32', '0.10', '1.23', '4.74'),
    ('228', 'Manga, Haden, crua', 'manga haden cru', 'Frutas e derivados', '63.50', '16.66', '0.41', '0.26', '1.58', '0.55'),
    ('229', 'Manga, Palmer, crua', 'manga palmer cru', 'Frutas e derivados', '72.49', '19.35', '0.41', '0.17', '1.63', '1.86'),
    ('230', 'Manga, polpa, congelada', 'manga polpa congelada', 'Frutas e derivados', '48.31', '12.52', '0.38', '0.23', '1.07', '6.73'),
    ('231', 'Manga, Tommy Atkins, crua', 'manga tommy atkins cru', 'Frutas e derivados', '50.69', '12.77', '0.86', '0.22', '2.07', '0.00'),
    ('232', 'Maracujá, cru', 'maracuja cru', 'Frutas e derivados', '68.44', '12.26', '1.99', '2.10', '1.14', '1.58'),
    ('233', 'Maracujá, polpa, congelada', 'maracuja polpa congelada', 'Frutas e derivados', '38.76', '9.60', '0.81', '0.18', '0.51', '8.10'),
    ('234', 'Maracujá, suco concentrado, envasado', 'maracuja suco concentrado envasado', 'Frutas e derivados', '41.97', '9.64', '0.77', '0.19', '0.35', '21.69'),
    ('235', 'Melancia, crua', 'melancia cru', 'Frutas e derivados', '32.61', '8.14', '0.88', '0.00', '0.12', '0.00'),
    ('236', 'Melão, cru', 'melao cru', 'Frutas e derivados', '29.37', '7.53', '0.68', '0.00', '0.25', '11.17'),
    ('237', 'Mexerica, Murcote, crua', 'tangerina murcote cru', 'Frutas e derivados', '57.59', '14.86', '0.88', '0.13', '3.07', '1.17'),
    ('238', 'Mexerica, Rio, crua', 'tangerina rio cru', 'Frutas e derivados', '36.87', '9.34', '0.65', '0.13', '2.73', '1.82'),
    ('239', 'Morango, cru', 'morango cru', 'Frutas e derivados', '30.15', '6.82', '0.89', '0.31', '1.72', '0.00'),
    ('240', 'Nêspera, crua', 'nespera cru', 'Frutas e derivados', '42.54', '11.53', '0.31', '0.00', '2.96', '0.00'),
    ('241', 'Pequi, cru', 'pequi cru', 'Frutas e derivados', '204.97', '12.97', '2.34', '17.97', '19.04', '0.00'),
    ('242', 'Pêra, Park, crua', 'pera park cru', 'Frutas e derivados', '60.59', '16.07', '0.24', '0.23', '2.98', '0.98'),
    ('243', 'Pêra, Williams, crua', 'pera williams cru', 'Frutas e derivados', '53.31', '14.02', '0.57', '0.11', '3.01', '0.00'),
    ('244', 'Pêssego, Aurora, cru', 'pessego aurora cru', 'Frutas e derivados', '36.33', '9.32', '0.83', '0.00', '1.42', '0.00'),
    ('245', 'Pêssego, enlatado, em calda', 'pessego enlatado calda', 'Frutas e derivados', '63.14', '16.88', '0.71', '0.00', '1.02', '3.20'),
    ('246', 'Pinha, crua', 'pinha cru', 'Frutas e derivados', '88.47', '22.45', '1.49', '0.32', '3.36', '1.34'),
    ('247', 'Pitanga, crua', 'pitanga cru', 'Frutas e derivados', '41.42', '10.24', '0.93', '0.17', '3.24', '1.70'),
    ('248', 'Pitanga, polpa, congelada', 'pitanga polpa congelada', 'Frutas e derivados', '19.11', '4.76', '0.29', '0.12', '0.74', '5.03'),
    ('249', 'Romã, crua', 'roma cru', 'Frutas e derivados', '55.74', '15.11', '0.40', '0.00', '0.44', '0.59'),
    ('250', 'Tamarindo, cru', 'tamarindo cru', 'Frutas e derivados', '275.70', '72.53', '3.21', '0.46', '6.45', '0.36'),
    ('251', 'Tangerina, Poncã, crua', 'tangerina tangerina cru', 'Frutas e derivados', '37.83', '9.61', '0.85', '0.07', '0.94', '0.00'),
    ('252', 'Tangerina, Poncã, suco', 'tangerina tangerina suco', 'Frutas e derivados', '36.11', '8.80', '0.52', '0.00', '0.00', '0.00'),
    ('253', 'Tucumã, cru', 'tucuma cru', 'Frutas e derivados', '262.02', '26.47', '2.09', '19.08', '12.65', '3.89'),
    ('254', 'Umbu, cru', 'umbu cru', 'Frutas e derivados', '37.02', '9.40', '0.84', '0.00', '1.98', '0.00'),
    ('255', 'Umbu, polpa, congelada', 'umbu polpa congelada', 'Frutas e derivados', '33.94', '8.79', '0.51', '0.07', '1.34', '5.77'),
    ('256', 'Uva, Itália, crua', 'uva italia cru', 'Frutas e derivados', '52.87', '13.57', '0.75', '0.20', '0.92', '0.00'),
    ('257', 'Uva, Rubi, crua', 'uva rubi cru', 'Frutas e derivados', '49.06', '12.70', '0.61', '0.16', '0.93', '7.92'),
    ('258', 'Uva, suco concentrado, envasado', 'uva suco concentrado envasado', 'Frutas e derivados', '57.66', '14.71', '0.00', '0.00', '0.23', '9.58'),
    ('259', 'Azeite, de dendê', 'azeite dende', 'Gorduras e óleos', '884.00', None, None, '100.00', None, None),
    ('260', 'Azeite, de oliva, extra virgem', 'azeite oliva extra virgem', 'Gorduras e óleos', '884.00', None, None, '100.00', None, None),
    ('261', 'Manteiga, com sal', 'manteiga sal', 'Gorduras e óleos', '725.97', '0.06', '0.41', '82.36', None, '578.69'),
    ('262', 'Manteiga, sem sal', 'manteiga sem sal', 'Gorduras e óleos', '757.54', '0.00', '0.40', '86.04', None, '3.85'),
    ('263', 'Margarina, com óleo hidrogenado, com sal (65% de lipídeos)', 'margarina oleo hidrogenado sal 65 lipideos', 'Gorduras e óleos', '596.12', '0.00', '0.00', '67.43', None, '894.04'),
    ('264', 'Margarina, com óleo hidrogenado, sem sal (80% de lipídeos)', 'margarina oleo hidrogenado sem sal 80 lipideos', 'Gorduras e óleos', '722.53', '0.00', '0.00', '81.73', None, '77.89'),
    ('265', 'Margarina, com óleo interesterificado, com sal (65%de lipídeos)', 'margarina oleo interesterificado sal 65 lipideos', 'Gorduras e óleos', '594.45', '0.00', '0.00', '67.25', None, '560.80'),
    ('266', 'Margarina, com óleo interesterificado, sem sal (65% de lipídeos)', 'margarina oleo interesterificado sem sal 65 lipideos', 'Gorduras e óleos', '593.14', '0.00', '0.00', '67.10', None, '33.19'),
    ('267', 'Óleo, de babaçu', 'oleo babacu', 'Gorduras e óleos', '884.00', None, None, '100.00', None, None),
    ('268', 'Óleo, de canola', 'oleo canola', 'Gorduras e óleos', '884.00', None, None, '100.00', None, None),
    ('269', 'Óleo, de girassol', 'oleo girassol', 'Gorduras e óleos', '884.00', None, None, '100.00', None, None),
    ('270', 'Óleo, de milho', 'oleo milho', 'Gorduras e óleos', '884.00', None, None, '100.00', None, None),
    ('271', 'Óleo, de pequi', 'oleo pequi', 'Gorduras e óleos', '884.00', None, None, '100.00', None, None),
    ('272', 'Óleo, de soja', 'oleo soja', 'Gorduras e óleos', '884.00', None, None, '100.00', None, None),
    ('273', 'Abadejo, filé, congelado, assado', 'abadejo file congelado assado', 'Pescados e frutos do mar', '111.62', '0.00', '23.53', '1.24', None, '334.39'),
    ('274', 'Abadejo, filé, congelado, cozido', 'abadejo file congelado cozido', 'Pescados e frutos do mar', '91.10', '0.00', '19.35', '0.94', None, '189.34'),
    ('275', 'Abadejo, filé, congelado, cru', 'abadejo file congelado cru', 'Pescados e frutos do mar', '59.11', '0.00', '13.08', '0.36', None, '78.52'),
    ('276', 'Abadejo, filé, congelado, grelhado', 'abadejo file congelado grelhado', 'Pescados e frutos do mar', '129.64', '0.00', '27.61', '1.30', None, '305.09'),
    ('277', 'Atum, conserva em óleo', 'atum conserva oleo', 'Pescados e frutos do mar', '165.91', '0.00', '26.19', '6.00', None, '362.15'),
    ('278', 'Atum, fresco, cru', 'atum fresco cru', 'Pescados e frutos do mar', '117.50', '0.00', '25.68', '0.87', None, '30.30'),
    ('279', 'Bacalhau, salgado, cru', 'bacalhau salgado cru', 'Pescados e frutos do mar', '135.89', '0.00', '29.04', '1.32', None, '13585.06'),
    ('280', 'Bacalhau, salgado, refogado', 'bacalhau salgado refogado', 'Pescados e frutos do mar', '139.66', '1.22', '23.98', '3.61', None, '1256.28'),
    ('281', 'Cação, posta, com farinha de trigo, frita', 'cacao posta farinha trigo frito', 'Pescados e frutos do mar', '208.33', '3.10', '24.95', '9.95', '0.54', '160.03'),
    ('282', 'Cação, posta, cozida', 'cacao posta cozido', 'Pescados e frutos do mar', '116.01', '0.00', '25.59', '0.75', None, '114.91'),
    ('283', 'Cação, posta, crua', 'cacao posta cru', 'Pescados e frutos do mar', '83.33', '0.00', '17.85', '0.79', None, '176.02'),
    ('284', 'Camarão, Rio Grande, grande, cozido', 'camarao rio grande grande cozido', 'Pescados e frutos do mar', '90.01', '0.00', '18.97', '1.00', None, '366.55'),
    ('285', 'Camarão, Rio Grande, grande, cru', 'camarao rio grande grande cru', 'Pescados e frutos do mar', '47.18', '0.00', '9.99', '0.50', None, '201.13'),
    ('286', 'Camarão, Sete Barbas, sem cabeça, com casca, frito', 'camarao sete barbas sem cabeca casca frito', 'Pescados e frutos do mar', '231.25', '2.88', '18.39', '15.62', None, '99.06'),
    ('287', 'Caranguejo, cozido', 'caranguejo cozido', 'Pescados e frutos do mar', '82.72', '0.00', '18.48', '0.42', None, '360.11'),
    ('288', 'Corimba, cru', 'corimba cru', 'Pescados e frutos do mar', '128.16', '0.00', '17.37', '5.99', None, '47.01'),
    ('289', 'Corimbatá, assado', 'corimbata assado', 'Pescados e frutos do mar', '261.45', '0.00', '19.90', '19.57', None, '40.43'),
    ('290', 'Corimbatá, cozido', 'corimbata cozido', 'Pescados e frutos do mar', '238.70', '0.00', '20.13', '16.93', None, '37.17'),
    ('291', 'Corvina de água doce, crua', 'corvina agua doce cru', 'Pescados e frutos do mar', '101.01', '0.00', '18.92', '2.24', None, '45.09'),
    ('292', 'Corvina do mar, crua', 'corvina mar cru', 'Pescados e frutos do mar', '94.00', '0.00', '18.57', '1.58', None, '67.97'),
    ('293', 'Corvina grande, assada', 'corvina grande assado', 'Pescados e frutos do mar', '146.53', '0.00', '26.77', '3.57', None, '85.35'),
    ('294', 'Corvina grande, cozida', 'corvina grande cozido', 'Pescados e frutos do mar', '100.08', '0.00', '23.44', '2.56', None, '68.39'),
    ('295', 'Dourada de água doce, fresca', 'dourada agua doce fresca', 'Pescados e frutos do mar', '131.21', '0.00', '18.81', '5.64', None, '40.30'),
    ('296', 'Lambari, congelado, cru', 'lambari congelado cru', 'Pescados e frutos do mar', '130.84', '0.00', '16.81', '6.55', None, '47.92'),
    ('297', 'Lambari, congelado, frito', 'lambari congelado frito', 'Pescados e frutos do mar', '326.87', '0.00', '28.43', '22.78', None, '64.55'),
    ('298', 'Lambari, fresco, cru', 'lambari fresco cru', 'Pescados e frutos do mar', '151.60', '0.00', '15.65', '9.40', None, '41.11'),
    ('299', 'Manjuba, com farinha de trigo, frita', 'manjuba farinha trigo frito', 'Pescados e frutos do mar', '343.55', '10.24', '23.45', '22.59', '0.36', '36.52'),
    ('300', 'Manjuba, frita', 'manjuba frito', 'Pescados e frutos do mar', '349.33', '0.00', '30.14', '24.46', None, '40.61'),
    ('301', 'Merluza, filé, assado', 'merluza file assado', 'Pescados e frutos do mar', '121.91', '0.00', '26.60', '0.92', None, '119.95'),
    ('302', 'Merluza, filé, cru', 'merluza file cru', 'Pescados e frutos do mar', '89.13', '0.00', '16.61', '2.02', None, '79.50'),
    ('303', 'Merluza, filé, frito', 'merluza file frito', 'Pescados e frutos do mar', '191.63', '0.00', '26.93', '8.50', None, '89.96'),
    ('304', 'Pescada, branca, crua', 'pescada branca cru', 'Pescados e frutos do mar', '110.88', '0.00', '16.26', '4.59', None, '76.17'),
    ('305', 'Pescada, branca, frita', 'pescada branca frito', 'Pescados e frutos do mar', '223.04', '0.00', '27.36', '11.78', None, '107.23'),
    ('306', 'Pescada, filé, com farinha de trigo, frito', 'pescada file farinha trigo frito', 'Pescados e frutos do mar', '283.43', '5.03', '21.44', '19.12', '0.00', '90.51'),
    ('307', 'Pescada, filé, cru', 'pescada file cru', 'Pescados e frutos do mar', '107.21', '0.00', '16.65', '4.00', None, '77.50'),
    ('308', 'Pescada, filé, frito', 'pescada file frito', 'Pescados e frutos do mar', '154.27', '0.00', '28.59', '3.57', None, '114.91'),
    ('309', 'Pescada, filé, molho escabeche', 'pescada file molho escabeche', 'Pescados e frutos do mar', '141.96', '5.02', '11.75', '8.02', '0.78', '51.29'),
    ('310', 'Pescadinha, crua', 'pescadinha cru', 'Pescados e frutos do mar', '76.41', '0.00', '15.48', '1.14', None, '120.34'),
    ('311', 'Pintado, assado', 'pintado assado', 'Pescados e frutos do mar', '191.56', '0.00', '36.45', '3.98', None, '80.95'),
    ('312', 'Pintado, cru', 'pintado cru', 'Pescados e frutos do mar', '91.08', '0.00', '18.56', '1.31', None, '43.34'),
    ('313', 'Pintado, grelhado', 'pintado grelhado', 'Pescados e frutos do mar', '152.19', '0.00', '30.80', '2.29', None, '53.09'),
    ('314', 'Porquinho, cru', 'porquinho cru', 'Pescados e frutos do mar', '93.02', '0.00', '20.49', '0.61', None, '66.73'),
    ('315', 'Salmão, filé, com pele, fresco, grelhado', 'salmao file pele fresco grelhado', 'Pescados e frutos do mar', '228.73', '0.00', '23.92', '14.04', None, '85.14'),
    ('316', 'Salmão, sem pele, fresco, cru', 'salmao sem pele fresco cru', 'Pescados e frutos do mar', '169.78', '0.00', '19.25', '9.71', None, '64.24'),
    ('317', 'Salmão, sem pele, fresco, grelhado', 'salmao sem pele fresco grelhado', 'Pescados e frutos do mar', '242.71', '0.00', '26.14', '14.53', None, '95.81'),
    ('318', 'Sardinha, assada', 'sardinha assado', 'Pescados e frutos do mar', '164.35', '0.00', '32.18', '2.99', None, '74.47'),
    ('319', 'Sardinha, conserva em óleo', 'sardinha conserva oleo', 'Pescados e frutos do mar', '284.98', '0.00', '15.94', '24.05', None, '665.84'),
    ('320', 'Sardinha, frita', 'sardinha frito', 'Pescados e frutos do mar', '257.04', '0.00', '33.38', '12.69', None, '60.10'),
    ('321', 'Sardinha, inteira, crua', 'sardinha inteira cru', 'Pescados e frutos do mar', '113.90', '0.00', '21.08', '2.65', None, '60.39'),
    ('322', 'Tucunaré, filé, congelado, cru', 'tucunare file congelado cru', 'Pescados e frutos do mar', '87.69', '0.00', '17.96', '1.22', None, '56.55'),
    ('323', 'Apresuntado', 'apresuntado', 'Carnes e derivados', '128.86', '2.86', '13.45', '6.69', None, '942.93'),
    ('324', 'Caldo de carne, tablete', 'caldo carne tablete', 'Carnes e derivados', '240.62', '15.05', '7.82', '16.57', '0.58', '22179.67'),
    ('325', 'Caldo de galinha, tablete', 'caldo galinha tablete', 'Carnes e derivados', '251.45', '10.65', '6.28', '20.42', '11.81', '22299.90'),
    ('326', 'Carne, bovina, acém, moído, cozido', 'carne acem moido cozido', 'Carnes e derivados', '212.42', '0.00', '26.69', '10.92', None, '52.36'),
    ('327', 'Carne, bovina, acém, moído, cru', 'carne acem moido cru', 'Carnes e derivados', '136.56', '0.00', '19.42', '5.95', None, '48.61'),
    ('328', 'Carne, bovina, acém, sem gordura, cozido', 'carne acem sem gordura cozido', 'Carnes e derivados', '214.61', '0.00', '27.27', '10.88', None, '56.17'),
    ('329', 'Carne, bovina, acém, sem gordura, cru', 'carne acem sem gordura cru', 'Carnes e derivados', '144.03', '0.00', '20.82', '6.11', None, '49.85'),
    ('330', 'Carne, bovina, almôndegas, cruas', 'carne almondegas cru', 'Carnes e derivados', '189.26', '9.79', '12.31', '11.20', None, '621.25'),
    ('331', 'Carne, bovina, almôndegas, fritas', 'carne almondegas frito', 'Carnes e derivados', '271.81', '14.29', '18.16', '15.78', None, '1030.26'),
    ('332', 'Carne, bovina, bucho, cozido', 'carne bucho cozido', 'Carnes e derivados', '133.02', '0.00', '21.64', '4.50', None, '38.20'),
    ('333', 'Carne, bovina, bucho, cru', 'carne bucho cru', 'Carnes e derivados', '137.30', '0.00', '20.53', '5.50', None, '45.00'),
    ('334', 'Carne, bovina, capa de contra-filé, com gordura, crua', 'carne capa contra file gordura cru', 'Carnes e derivados', '216.91', '0.00', '19.20', '14.96', None, '57.54'),
    ('335', 'Carne, bovina, capa de contra-filé, com gordura, grelhada', 'carne capa contra file gordura grelhado', 'Carnes e derivados', '311.70', '0.00', '30.69', '20.03', None, '80.51'),
    ('336', 'Carne, bovina, capa de contra-filé, sem gordura, crua', 'carne capa contra file sem gordura cru', 'Carnes e derivados', '131.06', '0.00', '21.54', '4.33', None, '79.17'),
    ('337', 'Carne, bovina, capa de contra-filé, sem gordura, grelhada', 'carne capa contra file sem gordura grelhado', 'Carnes e derivados', '239.44', '0.00', '35.06', '9.95', None, '82.75'),
    ('338', 'Carne, bovina, charque, cozido', 'carne charque cozido', 'Carnes e derivados', '262.78', '0.00', '36.36', '11.92', None, '1442.70'),
    ('339', 'Carne, bovina, charque, cru', 'carne charque cru', 'Carnes e derivados', '248.86', '0.00', '22.71', '16.84', None, '5875.03'),
    ('340', 'Carne, bovina, contra-filé, à milanesa', 'carne contra file milanesa', 'Carnes e derivados', '351.59', '12.17', '20.61', '24.00', '0.37', '77.09'),
    ('341', 'Carne, bovina, contra-filé de costela, cru', 'carne contra file costela cru', 'Carnes e derivados', '202.44', '0.00', '19.80', '13.07', None, '38.52'),
    ('342', 'Carne, bovina, contra-filé de costela, grelhado', 'carne contra file costela grelhado', 'Carnes e derivados', '274.91', '0.00', '29.88', '16.33', None, '50.88'),
    ('343', 'Carne, bovina, contra-filé, com gordura, cru', 'carne contra file gordura cru', 'Carnes e derivados', '205.86', '0.00', '21.15', '12.81', None, '44.13'),
    ('344', 'Carne, bovina, contra-filé, com gordura, grelhado', 'carne contra file gordura grelhado', 'Carnes e derivados', '278.05', '0.00', '32.40', '15.49', None, '57.07'),
    ('345', 'Carne, bovina, contra-filé, sem gordura, cru', 'carne contra file sem gordura cru', 'Carnes e derivados', '156.62', '0.00', '24.00', '6.00', None, '52.89'),
    ('346', 'Carne, bovina, contra-filé, sem gordura, grelhado', 'carne contra file sem gordura grelhado', 'Carnes e derivados', '193.69', '0.00', '35.88', '4.49', None, '57.51'),
    ('347', 'Carne, bovina, costela, assada', 'carne costela assado', 'Carnes e derivados', '373.04', '0.00', '28.81', '27.72', None, '91.86'),
    ('348', 'Carne, bovina, costela, crua', 'carne costela cru', 'Carnes e derivados', '357.72', '0.00', '16.71', '31.75', None, '70.00'),
    ('349', 'Carne, bovina, coxão duro, sem gordura, cozido', 'carne coxao duro sem gordura cozido', 'Carnes e derivados', '216.62', '0.00', '31.88', '8.92', None, '41.10'),
    ('350', 'Carne, bovina, coxão duro, sem gordura, cru', 'carne coxao duro sem gordura cru', 'Carnes e derivados', '147.97', '0.00', '21.51', '6.22', None, '48.55'),
    ('351', 'Carne, bovina, coxão mole, sem gordura, cozido', 'carne coxao mole sem gordura cozido', 'Carnes e derivados', '218.68', '0.00', '32.38', '8.91', None, '43.50'),
    ('352', 'Carne, bovina, coxão mole, sem gordura, cru', 'carne coxao mole sem gordura cru', 'Carnes e derivados', '169.07', '0.00', '21.23', '8.69', None, '60.53'),
    ('353', 'Carne, bovina, cupim, assado', 'carne cupim assado', 'Carnes e derivados', '330.10', '0.00', '28.63', '23.04', None, '71.59'),
    ('354', 'Carne, bovina, cupim, cru', 'carne cupim cru', 'Carnes e derivados', '221.40', '0.00', '19.54', '15.30', None, '46.86'),
    ('355', 'Carne, bovina, fígado, cru', 'carne figado cru', 'Carnes e derivados', '141.05', '1.11', '20.71', '5.36', None, '75.92'),
    ('356', 'Carne, bovina, fígado, grelhado', 'carne figado grelhado', 'Carnes e derivados', '225.03', '4.20', '29.86', '9.01', None, '82.19'),
    ('357', 'Carne, bovina, filé mingnon, sem gordura, cru', 'carne file mingnon sem gordura cru', 'Carnes e derivados', '142.86', '0.00', '21.60', '5.61', None, '48.86'),
    ('358', 'Carne, bovina, filé mingnon, sem gordura, grelhado', 'carne file mingnon sem gordura grelhado', 'Carnes e derivados', '219.70', '0.00', '32.80', '8.83', None, '57.91'),
    ('359', 'Carne, bovina, flanco, sem gordura, cozido', 'carne flanco sem gordura cozido', 'Carnes e derivados', '195.58', '0.00', '29.38', '7.77', None, '41.68'),
    ('360', 'Carne, bovina, flanco, sem gordura, cru', 'carne flanco sem gordura cru', 'Carnes e derivados', '141.46', '0.00', '20.00', '6.22', None, '54.22'),
    ('361', 'Carne, bovina, fraldinha, com gordura, cozida', 'carne fraldinha gordura cozido', 'Carnes e derivados', '338.45', '0.00', '24.24', '26.05', None, '38.78'),
    ('362', 'Carne, bovina, fraldinha, com gordura, crua', 'carne fraldinha gordura cru', 'Carnes e derivados', '220.72', '0.00', '17.58', '16.15', None, '51.20'),
    ('363', 'Carne, bovina, lagarto, cozido', 'carne lagarto cozido', 'Carnes e derivados', '222.47', '0.00', '32.86', '9.11', None, '47.54'),
    ('364', 'Carne, bovina, lagarto, cru', 'carne lagarto cru', 'Carnes e derivados', '134.86', '0.00', '20.54', '5.23', None, '53.56'),
    ('365', 'Carne, bovina, língua, cozida', 'carne lingua cozido', 'Carnes e derivados', '314.90', '0.00', '21.37', '24.80', None, '59.06'),
    ('366', 'Carne, bovina, língua, crua', 'carne lingua cru', 'Carnes e derivados', '215.25', '0.00', '17.09', '15.77', None, '73.05'),
    ('367', 'Carne, bovina, maminha, crua', 'carne maminha cru', 'Carnes e derivados', '152.77', '0.00', '20.93', '7.03', None, '37.42'),
    ('368', 'Carne, bovina, maminha, grelhada', 'carne maminha grelhado', 'Carnes e derivados', '153.09', '0.00', '30.74', '2.42', None, '58.12'),
    ('369', 'Carne, bovina, miolo de alcatra, sem gordura, cru', 'carne miolo alcatra sem gordura cru', 'Carnes e derivados', '162.87', '0.00', '21.61', '7.83', None, '43.05'),
    ('370', 'Carne, bovina, miolo de alcatra, sem gordura, grelhado', 'carne miolo alcatra sem gordura grelhado', 'Carnes e derivados', '241.36', '0.00', '31.93', '11.64', None, '51.62'),
    ('371', 'Carne, bovina, músculo, sem gordura, cozido', 'carne musculo sem gordura cozido', 'Carnes e derivados', '193.80', '0.00', '31.23', '6.70', None, '61.79'),
    ('372', 'Carne, bovina, músculo, sem gordura, cru', 'carne musculo sem gordura cru', 'Carnes e derivados', '141.58', '0.00', '21.56', '5.49', None, '66.08'),
    ('373', 'Carne, bovina, paleta, com gordura, crua', 'carne paleta gordura cru', 'Carnes e derivados', '158.71', '0.00', '21.41', '7.46', None, '64.90'),
    ('374', 'Carne, bovina, paleta, sem gordura, cozida', 'carne paleta sem gordura cozido', 'Carnes e derivados', '193.65', '0.00', '29.72', '7.40', None, '57.62'),
    ('375', 'Carne, bovina, paleta, sem gordura, crua', 'carne paleta sem gordura cru', 'Carnes e derivados', '140.94', '0.00', '21.03', '5.67', None, '65.86'),
    ('376', 'Carne, bovina, patinho, sem gordura, cru', 'carne patinho sem gordura cru', 'Carnes e derivados', '133.47', '0.00', '21.72', '4.51', None, '49.13'),
    ('377', 'Carne, bovina, patinho, sem gordura, grelhado', 'carne patinho sem gordura grelhado', 'Carnes e derivados', '219.26', '0.00', '35.90', '7.31', None, '60.29'),
    ('378', 'Carne, bovina, peito, sem gordura, cozido', 'carne peito sem gordura cozido', 'Carnes e derivados', '338.47', '0.00', '22.25', '26.99', None, '55.71'),
    ('379', 'Carne, bovina, peito, sem gordura, cru', 'carne peito sem gordura cru', 'Carnes e derivados', '259.28', '0.00', '17.56', '20.43', None, '63.76'),
    ('380', 'Carne, bovina, picanha, com gordura, crua', 'carne picanha gordura cru', 'Carnes e derivados', '212.88', '0.00', '18.82', '14.69', None, '37.62'),
    ('381', 'Carne, bovina, picanha, com gordura, grelhada', 'carne picanha gordura grelhado', 'Carnes e derivados', '288.77', '0.00', '26.42', '19.51', None, '60.00'),
    ('382', 'Carne, bovina, picanha, sem gordura, crua', 'carne picanha sem gordura cru', 'Carnes e derivados', '133.52', '0.00', '21.25', '4.74', None, '61.15'),
    ('383', 'Carne, bovina, picanha, sem gordura, grelhada', 'carne picanha sem gordura grelhado', 'Carnes e derivados', '238.47', '0.00', '31.91', '11.33', None, '60.66'),
    ('384', 'Carne, bovina, seca, cozida', 'carne seca cozido', 'Carnes e derivados', '312.80', '0.00', '26.93', '21.93', None, '1943.18'),
    ('385', 'Carne, bovina, seca, crua', 'carne seca cru', 'Carnes e derivados', '312.75', '0.00', '19.66', '25.37', None, '4439.55'),
    ('386', 'Coxinha de frango, frita', 'coxinha frango frito', 'Carnes e derivados', '283.05', '34.52', '9.61', '11.84', '4.97', '532.13'),
    ('387', 'Croquete, de carne, cru', 'croquete carne cru', 'Carnes e derivados', '245.77', '13.95', '12.04', '15.56', None, '710.64'),
    ('388', 'Croquete, de carne, frito', 'croquete carne frito', 'Carnes e derivados', '346.74', '18.15', '16.86', '22.67', None, '916.41'),
    ('389', 'Empada de frango, pré-cozida, assada', 'empada frango pre cozido assado', 'Carnes e derivados', '358.19', '47.49', '6.94', '15.61', '2.16', '524.93'),
    ('390', 'Empada, de frango, pré-cozida', 'empada frango pre cozido', 'Carnes e derivados', '377.48', '35.53', '7.34', '22.89', '2.22', '770.73'),
    ('391', 'Frango, asa, com pele, crua', 'frango asa pele cru', 'Carnes e derivados', '213.19', '0.00', '18.10', '15.07', None, '96.30'),
    ('392', 'Frango, caipira, inteiro, com pele, cozido', 'frango caipira inteiro pele cozido', 'Carnes e derivados', '242.89', '0.00', '23.88', '15.62', None, '56.09'),
    ('393', 'Frango, caipira, inteiro, sem pele, cozido', 'frango caipira inteiro sem pele cozido', 'Carnes e derivados', '195.76', '0.00', '29.58', '7.70', None, '53.24'),
    ('394', 'Frango, coração, cru', 'frango coracao cru', 'Carnes e derivados', '221.50', '0.00', '12.58', '18.60', None, '95.06'),
    ('395', 'Frango, coração, grelhado', 'frango coracao grelhado', 'Carnes e derivados', '207.27', '0.61', '22.44', '12.10', None, '128.24'),
    ('396', 'Frango, coxa, com pele, assada', 'frango coxa pele assado', 'Carnes e derivados', '215.12', '0.06', '28.49', '10.36', None, '94.84'),
    ('397', 'Frango, coxa, com pele, crua', 'frango coxa pele cru', 'Carnes e derivados', '161.47', '0.00', '17.09', '9.81', None, '94.96'),
    ('398', 'Frango, coxa, sem pele, cozida', 'frango coxa sem pele cozido', 'Carnes e derivados', '167.43', '0.00', '26.86', '5.85', None, '64.34'),
    ('399', 'Frango, coxa, sem pele, crua', 'frango coxa sem pele cru', 'Carnes e derivados', '119.95', '0.02', '17.81', '4.86', None, '98.37'),
    ('400', 'Frango, fígado, cru', 'frango figado cru', 'Carnes e derivados', '106.48', '0.00', '17.59', '3.49', None, '82.43'),
    ('401', 'Frango, filé, à milanesa', 'frango file milanesa', 'Carnes e derivados', '220.87', '7.51', '28.46', '7.79', '1.13', '122.33'),
    ('402', 'Frango, inteiro, com pele, cru', 'frango inteiro pele cru', 'Carnes e derivados', '226.32', '0.00', '16.44', '17.31', None, '62.88'),
    ('403', 'Frango, inteiro, sem pele, assado', 'frango inteiro sem pele assado', 'Carnes e derivados', '187.34', '0.00', '28.03', '7.50', None, '70.27'),
    ('404', 'Frango, inteiro, sem pele, cozido', 'frango inteiro sem pele cozido', 'Carnes e derivados', '170.39', '0.00', '24.99', '7.06', None, '50.89'),
    ('405', 'Frango, inteiro, sem pele, cru', 'frango inteiro sem pele cru', 'Carnes e derivados', '129.10', '0.00', '20.59', '4.57', None, '72.96'),
    ('406', 'Frango, peito, com pele, assado', 'frango peito pele assado', 'Carnes e derivados', '211.68', '0.00', '33.42', '7.65', None, '55.70'),
    ('407', 'Frango, peito, com pele, cru', 'frango peito pele cru', 'Carnes e derivados', '149.47', '0.00', '20.78', '6.73', None, '62.31'),
    ('408', 'Frango, peito, sem pele, cozido', 'frango peito sem pele cozido', 'Carnes e derivados', '162.87', '0.00', '31.47', '3.16', None, '36.17'),
    ('409', 'Frango, peito, sem pele, cru', 'frango peito sem pele cru', 'Carnes e derivados', '119.16', '0.00', '21.53', '3.02', None, '56.14'),
    ('410', 'Frango, peito, sem pele, grelhado', 'frango peito sem pele grelhado', 'Carnes e derivados', '159.19', '0.00', '32.03', '2.48', None, '50.25'),
    ('411', 'Frango, sobrecoxa, com pele, assada', 'frango sobrecoxa pele assado', 'Carnes e derivados', '259.60', '0.00', '28.70', '15.19', None, '95.94'),
    ('412', 'Frango, sobrecoxa, com pele, crua', 'frango sobrecoxa pele cru', 'Carnes e derivados', '254.53', '0.00', '15.46', '20.90', None, '68.27'),
    ('413', 'Frango, sobrecoxa, sem pele, assada', 'frango sobrecoxa sem pele assado', 'Carnes e derivados', '232.88', '0.00', '29.18', '12.01', None, '106.08'),
    ('414', 'Frango, sobrecoxa, sem pele, crua', 'frango sobrecoxa sem pele cru', 'Carnes e derivados', '161.80', '0.00', '17.57', '9.62', None, '79.75'),
    ('415', 'Hambúrguer, bovino, cru', 'hamburguer bovino cru', 'Carnes e derivados', '214.84', '4.15', '13.16', '16.18', None, '869.46'),
    ('416', 'Hambúrguer, bovino, frito', 'hamburguer bovino frito', 'Carnes e derivados', '258.28', '6.32', '19.97', '17.01', None, '1251.80'),
    ('417', 'Hambúrguer, bovino, grelhado', 'hamburguer bovino grelhado', 'Carnes e derivados', '209.83', '11.33', '13.16', '12.43', None, '1090.33'),
    ('418', 'Lingüiça, frango, crua', 'linguica frango cru', 'Carnes e derivados', '218.11', '0.00', '14.24', '17.44', None, '1125.81'),
    ('419', 'Lingüiça, frango, frita', 'linguica frango frito', 'Carnes e derivados', '245.46', '0.00', '18.32', '18.54', None, '1373.89'),
    ('420', 'Lingüiça, frango, grelhada', 'linguica frango grelhado', 'Carnes e derivados', '243.66', '0.00', '18.19', '18.40', None, '1351.49'),
    ('421', 'Lingüiça, porco, crua', 'linguica porco cru', 'Carnes e derivados', '227.20', '0.00', '16.06', '17.58', None, '1175.72'),
    ('422', 'Lingüiça, porco, frita', 'linguica porco frito', 'Carnes e derivados', '279.54', '0.00', '20.45', '21.31', None, '1431.59'),
    ('423', 'Lingüiça, porco, grelhada', 'linguica porco grelhado', 'Carnes e derivados', '296.49', '0.00', '23.17', '21.90', None, '1455.86'),
    ('424', 'Mortadela', 'mortadela', 'Carnes e derivados', '268.82', '5.82', '11.95', '21.65', None, '1212.17'),
    ('425', 'Peru, congelado, assado', 'peru congelado assado', 'Carnes e derivados', '163.07', '0.00', '26.20', '5.68', None, '627.88'),
    ('426', 'Peru, congelado, cru', 'peru congelado cru', 'Carnes e derivados', '93.72', '0.00', '18.08', '1.83', None, '710.68'),
    ('427', 'Porco, bisteca, crua', 'porco bisteca cru', 'Carnes e derivados', '164.12', '0.00', '21.50', '8.02', None, '54.29'),
    ('428', 'Porco, bisteca, frita', 'porco bisteca frito', 'Carnes e derivados', '311.17', '0.00', '33.75', '18.52', None, '63.03'),
    ('429', 'Porco, bisteca, grelhada', 'porco bisteca grelhado', 'Carnes e derivados', '280.08', '0.00', '28.89', '17.38', None, '51.45'),
    ('430', 'Porco, costela, assada', 'porco costela assado', 'Carnes e derivados', '402.17', '0.00', '30.22', '30.28', None, '62.68'),
    ('431', 'Porco, costela, crua', 'porco costela cru', 'Carnes e derivados', '255.61', '0.00', '18.00', '19.82', None, '87.98'),
    ('432', 'Porco, lombo, assado', 'porco lombo assado', 'Carnes e derivados', '210.23', '0.00', '35.73', '6.40', None, '38.92'),
    ('433', 'Porco, lombo, cru', 'porco lombo cru', 'Carnes e derivados', '175.63', '0.00', '22.60', '8.77', None, '53.07'),
    ('434', 'Porco, orelha, salgada, crua', 'porco orelha salgada cru', 'Carnes e derivados', '258.49', '0.00', '18.52', '19.89', None, '615.60'),
    ('435', 'Porco, pernil, assado', 'porco pernil assado', 'Carnes e derivados', '262.26', '0.00', '32.13', '13.86', None, '62.41'),
    ('436', 'Porco, pernil, cru', 'porco pernil cru', 'Carnes e derivados', '186.06', '0.00', '20.13', '11.10', None, '101.89'),
    ('437', 'Porco, rabo, salgado, cru', 'porco rabo salgado cru', 'Carnes e derivados', '377.42', '0.00', '15.58', '34.47', None, '1157.67'),
    ('438', 'Presunto, com capa de gordura', 'presunto capa gordura', 'Carnes e derivados', '127.85', '1.40', '14.37', '6.77', None, '1020.77'),
    ('439', 'Presunto, sem capa de gordura', 'presunto sem capa gordura', 'Carnes e derivados', '93.74', '2.15', '14.29', '2.71', None, '1039.19'),
    ('440', 'Quibe, assado', 'quibe assado', 'Carnes e derivados', '136.23', '12.86', '14.59', '2.68', '1.90', '39.89'),
    ('441', 'Quibe, cru', 'quibe cru', 'Carnes e derivados', '109.49', '10.77', '12.35', '1.67', '1.65', '38.77'),
    ('442', 'Quibe, frito', 'quibe frito', 'Carnes e derivados', '253.83', '12.34', '14.89', '15.80', None, '835.83'),
    ('443', 'Salame', 'salame', 'Carnes e derivados', '397.84', '2.91', '25.81', '30.64', None, '1574.17'),
    ('444', 'Toucinho, cru', 'toucinho cru', 'Carnes e derivados', '592.53', '0.00', '11.48', '60.26', None, '49.59'),
    ('445', 'Toucinho, frito', 'toucinho frito', 'Carnes e derivados', '696.56', '0.00', '27.28', '64.31', None, '124.85'),
    ('446', 'Bebida láctea, pêssego', 'bebida lactea pessego', 'Leite e derivados', '55.16', '7.57', '2.13', '1.91', '0.29', '46.26'),
    ('447', 'Creme de Leite', 'creme leite', 'Leite e derivados', '221.48', '4.51', '1.51', '22.48', None, '51.72'),
    ('448', 'Iogurte, natural', 'iogurte natural', 'Leite e derivados', '51.49', '1.92', '4.06', '3.04', None, '51.62'),
    ('449', 'Iogurte, natural, desnatado', 'iogurte natural desnatado', 'Leite e derivados', '41.49', '5.77', '3.83', '0.32', None, '59.64'),
    ('450', 'Iogurte, sabor abacaxi', 'iogurte sabor abacaxi', 'Leite e derivados', None, None, None, None, None, None),
    ('451', 'Iogurte, sabor morango', 'iogurte sabor morango', 'Leite e derivados', '69.57', '9.69', '2.71', '2.33', '0.22', '37.66'),
    ('452', 'Iogurte, sabor pêssego', 'iogurte sabor pessego', 'Leite e derivados', '67.85', '9.43', '2.53', '2.34', '0.72', '36.96'),
    ('453', 'Leite, condensado', 'leite condensado', 'Leite e derivados', '312.57', '57.00', '7.67', '6.74', None, '93.80'),
    ('454', 'Leite, de cabra', 'leite cabra', 'Leite e derivados', '66.42', '5.25', '3.07', '3.75', None, '73.95'),
    ('455', 'Leite, de vaca, achocolatado', 'leite achocolatado', 'Leite e derivados', '82.82', '14.16', '2.10', '2.17', '0.65', '71.74'),
    ('456', 'Leite, de vaca, desnatado, pó', 'leite desnatado po', 'Leite e derivados', '361.61', '53.04', '34.69', '0.93', None, '431.67'),
    ('457', 'Leite, de vaca, desnatado, UHT', 'leite desnatado', 'Leite e derivados', None, None, None, None, None, '51.14'),
    ('458', 'Leite, de vaca, integral', 'leite integral', 'Leite e derivados', None, None, None, None, None, '63.76'),
    ('459', 'Leite, de vaca, integral, pó', 'leite integral po', 'Leite e derivados', '496.65', '39.18', '25.42', '26.90', None, '323.20'),
    ('460', 'Leite, fermentado', 'leite fermentado', 'Leite e derivados', '69.62', '15.67', '1.89', '0.10', None, '33.43'),
    ('461', 'Queijo, minas, frescal', 'queijo minas frescal', 'Leite e derivados', '264.27', '3.24', '17.41', '20.18', None, '31.23'),
    ('462', 'Queijo, minas, meia cura', 'queijo minas meia cura', 'Leite e derivados', '320.72', '3.57', '21.21', '24.61', None, '501.17'),
    ('463', 'Queijo, mozarela', 'queijo mozarela', 'Leite e derivados', '329.87', '3.05', '22.65', '25.18', None, '581.36'),
    ('464', 'Queijo, parmesão', 'queijo parmesao', 'Leite e derivados', '452.96', '1.66', '35.55', '33.53', None, '1844.08'),
    ('465', 'Queijo, pasteurizado', 'queijo pasteurizado', 'Leite e derivados', '303.08', '5.68', '9.36', '27.44', None, '780.43'),
    ('466', 'Queijo, petit suisse, morango', 'queijo petit suisse morango', 'Leite e derivados', '121.11', '18.46', '5.79', '2.84', None, '412.47'),
    ('467', 'Queijo, prato', 'queijo prato', 'Leite e derivados', '359.88', '1.88', '22.66', '29.11', None, '579.77'),
    ('468', 'Queijo, requeijão, cremoso', 'queijo requeijao', 'Leite e derivados', '256.58', '2.43', '9.63', '23.44', None, '557.92'),
    ('469', 'Queijo, ricota', 'queijo ricota', 'Leite e derivados', '139.73', '3.79', '12.60', '8.11', None, '282.58'),
    ('470', 'Bebida isotônica, sabores variados', 'bebida isotonica sabores variados', 'Bebidas (alcoólicas e não alcoólicas)', '25.61', '6.40', '0.00', '0.00', None, '44.08'),
    ('471', 'Café, infusão 10%', 'cafe infusao 10', 'Bebidas (alcoólicas e não alcoólicas)', '9.07', '1.48', '0.71', '0.07', None, '1.03'),
    ('472', 'Cana, aguardente', 'cana aguardente', 'Bebidas (alcoólicas e não alcoólicas)', '215.66', None, None, None, None, '3.15'),
    ('473', 'Cana, caldo de', 'cana caldo', 'Bebidas (alcoólicas e não alcoólicas)', '65.34', '18.15', '0.00', '0.00', '0.14', '0.00'),
    ('474', 'Cerveja, pilsen', 'cerveja pilsen', 'Bebidas (alcoólicas e não alcoólicas)', '40.72', '3.32', '0.56', '0.00', None, '4.23'),
    ('475', 'Chá, erva-doce, infusão 5%', 'cha erva doce infusao 5', 'Bebidas (alcoólicas e não alcoólicas)', '1.40', '0.39', '0.00', '0.00', None, '0.63'),
    ('476', 'Chá, mate, infusão 5%', 'cha mate infusao 5', 'Bebidas (alcoólicas e não alcoólicas)', '2.73', '0.64', '0.00', '0.05', None, '0.00'),
    ('477', 'Chá, preto, infusão 5%', 'cha preto infusao 5', 'Bebidas (alcoólicas e não alcoólicas)', '2.25', '0.63', '0.00', '0.00', None, '0.00'),
    ('478', 'Coco, água de', 'coco agua', 'Bebidas (alcoólicas e não alcoólicas)', '21.51', '5.28', '0.00', '0.00', '0.13', '1.78'),
    ('479', 'Refrigerante, tipo água tônica', 'refrigerante tipo agua tonica', 'Bebidas (alcoólicas e não alcoólicas)', '30.78', '7.95', '0.00', '0.00', None, '8.29'),
    ('480', 'Refrigerante, tipo cola', 'refrigerante tipo cola', 'Bebidas (alcoólicas e não alcoólicas)', '33.51', '8.66', '0.00', '0.00', None, '7.12'),
    ('481', 'Refrigerante, tipo guaraná', 'refrigerante tipo guarana', 'Bebidas (alcoólicas e não alcoólicas)', '38.70', '10.00', '0.00', '0.00', None, '9.01'),
    ('482', 'Refrigerante, tipo laranja', 'refrigerante tipo laranja', 'Bebidas (alcoólicas e não alcoólicas)', '45.63', '11.79', '0.00', '0.00', None, '9.27'),
    ('483', 'Refrigerante, tipo limão', 'refrigerante tipo limao', 'Bebidas (alcoólicas e não alcoólicas)', '39.72', '10.26', '0.00', '0.00', None, '8.80'),
    ('484', 'Omelete, de queijo', 'omelete queijo', 'Ovos e derivados', '268.01', '0.44', '15.57', '22.01', None, '216.05'),
    ('485', 'Ovo, de codorna, inteiro, cru', 'ovo codorna inteiro cru', 'Ovos e derivados', '176.89', '0.77', '13.69', '12.68', None, '128.99'),
    ('486', 'Ovo, de galinha, clara, cozida/10minutos', 'ovo clara cozido', 'Ovos e derivados', '59.44', '0.00', '13.45', '0.09', None, '180.54'),
    ('487', 'Ovo, de galinha, gema, cozida/10minutos', 'ovo gema cozido', 'Ovos e derivados', '352.67', '1.56', '15.90', '30.78', None, '44.91'),
    ('488', 'Ovo, de galinha, inteiro, cozido/10minutos', 'ovo cozido', 'Ovos e derivados', '145.70', '0.61', '13.29', '9.48', None, '145.90'),
    ('489', 'Ovo, de galinha, inteiro, cru', 'ovo cru', 'Ovos e derivados', '143.11', '1.64', '13.03', '8.90', None, '167.91'),
    ('490', 'Ovo, de galinha, inteiro, frito', 'ovo frito', 'Ovos e derivados', '240.19', '1.19', '15.62', '18.59', None, '166.11'),
    ('491', 'Achocolatado, pó', 'achocolatado po', 'Produtos açucarados', '401.02', '91.18', '4.20', '2.17', '3.89', '64.79'),
    ('492', 'Açúcar, cristal', 'acucar cristal', 'Produtos açucarados', '386.85', '99.61', '0.32', '0.00', None, '0.00'),
    ('493', 'Açúcar, mascavo', 'acucar mascavo', 'Produtos açucarados', '368.55', '94.45', '0.76', '0.09', None, '25.20'),
    ('494', 'Açúcar, refinado', 'acucar refinado', 'Produtos açucarados', '386.57', '99.54', '0.32', '0.00', None, '12.16'),
    ('495', 'Chocolate, ao leite', 'chocolate leite', 'Produtos açucarados', '539.59', '59.58', '7.22', '30.27', '2.17', '77.10'),
    ('496', 'Chocolate, ao leite, com castanha do Pará', 'chocolate leite castanha brasil', 'Produtos açucarados', '558.88', '55.38', '7.41', '34.19', '2.46', '64.05'),
    ('497', 'Chocolate, ao leite, dietético', 'chocolate leite dietetico', 'Produtos açucarados', '556.82', '56.32', '6.90', '33.77', '2.85', '84.71'),
    ('498', 'Chocolate, meio amargo', 'chocolate meio amargo', 'Produtos açucarados', '474.92', '62.42', '4.86', '29.86', '4.94', '8.87'),
    ('499', 'Cocada branca', 'cocada branca', 'Produtos açucarados', '448.85', '81.38', '1.12', '13.59', '3.57', '28.99'),
    ('500', 'Doce, de abóbora, cremoso', 'doce abobora cremoso', 'Produtos açucarados', '198.94', '54.61', '0.92', '0.21', '2.28', '0.00'),
    ('501', 'Doce, de leite, cremoso', 'doce leite cremoso', 'Produtos açucarados', '306.31', '59.49', '5.48', '5.99', None, '120.09'),
    ('502', 'Geléia, mocotó, natural', 'geleia mocoto natural', 'Produtos açucarados', '106.09', '24.23', '2.13', '0.07', None, '42.68'),
    ('503', 'Glicose de milho', 'glicose milho', 'Produtos açucarados', '292.12', '79.38', '0.00', '0.00', None, '58.93'),
    ('504', 'Maria mole', 'maria mole', 'Produtos açucarados', '301.24', '73.55', '3.81', '0.19', '0.67', '15.31'),
    ('505', 'Maria mole, coco queimado', 'maria mole coco queimado', 'Produtos açucarados', '306.63', '75.06', '3.93', '0.09', '0.64', '14.29'),
    ('506', 'Marmelada', 'marmelada', 'Produtos açucarados', '257.24', '70.76', '0.40', '0.14', '4.07', '10.88'),
    ('507', 'Mel, de abelha', 'mel abelha', 'Produtos açucarados', '309.24', '84.03', '0.00', '0.00', None, '6.04'),
    ('508', 'Melado', 'melado', 'Produtos açucarados', '296.51', '76.62', '0.00', '0.00', None, '4.01'),
    ('509', 'Quindim', 'quindim', 'Produtos açucarados', '411.35', '46.30', '4.74', '24.43', '3.22', '27.37'),
    ('510', 'Rapadura', 'rapadura', 'Produtos açucarados', '351.96', '90.79', '0.99', '0.07', None, '21.71'),
    ('511', 'Café, pó, torrado', 'cafe po torrado', 'Miscelâneas', '418.62', '65.75', '14.70', '11.95', '51.23', '1.13'),
    ('512', 'Capuccino, pó', 'capuccino po', 'Miscelâneas', '417.41', '73.61', '11.31', '8.63', '2.44', '382.29'),
    ('513', 'Fermento em pó, químico', 'fermento po quimico', 'Miscelâneas', '89.72', '43.91', '0.48', '0.07', None, '10052.41'),
    ('514', 'Fermento, biológico, levedura, tablete', 'fermento biologico levedura tablete', 'Miscelâneas', '89.79', '7.70', '16.96', '1.52', '4.17', '39.61'),
    ('515', 'Gelatina, sabores variados, pó', 'gelatina sabores variados po', 'Miscelâneas', '380.22', '89.22', '8.89', '0.00', None, '234.92'),
    ('516', 'Sal, dietético', 'sal dietetico', 'Miscelâneas', None, None, None, None, None, '23431.52'),
    ('517', 'Sal, grosso', 'sal grosso', 'Miscelâneas', None, None, None, None, None, '39943.20'),
    ('518', 'Shoyu', 'shoyu', 'Miscelâneas', '60.93', '11.65', '3.31', '0.33', None, '5024.21'),
    ('519', 'Tempero a base de sal', 'tempero base sal', 'Miscelâneas', '21.33', '2.07', '2.67', '0.26', '0.56', '32560.00'),
    ('520', 'Azeitona, preta, conserva', 'azeitona preta conserva', 'Outros alimentos industrializados', '194.15', '5.54', '1.16', '20.35', '4.56', '1566.66'),
    ('521', 'Azeitona, verde, conserva', 'azeitona verde conserva', 'Outros alimentos industrializados', '136.94', '4.10', '0.95', '14.22', '3.85', '1347.18'),
    ('522', 'Chantilly, spray, com gordura vegetal', 'chantilly spray gordura vegetal', 'Outros alimentos industrializados', '314.96', '16.86', '0.53', '27.27', None, '109.70'),
    ('523', 'Leite, de coco', 'leite coco', 'Outros alimentos industrializados', '166.16', '2.19', '1.01', '18.36', '0.68', '44.29'),
    ('524', 'Maionese, tradicional com ovos', 'maionese tradicional ovo', 'Outros alimentos industrializados', '302.15', '7.90', '0.58', '30.50', None, '786.83'),
    ('525', 'Acarajé', 'acaraje', 'Alimentos preparados', '289.21', '19.11', '8.35', '19.93', '9.36', '304.89'),
    ('526', 'Arroz carreteiro', 'arroz carreteiro', 'Alimentos preparados', '153.77', '11.58', '10.83', '7.12', '1.50', '1621.73'),
    ('527', 'Baião de dois, arroz e feijão-de-corda', 'baiao dois arroz feijao corda', 'Alimentos preparados', '135.68', '20.42', '6.24', '3.23', '5.07', '93.30'),
    ('528', 'Barreado', 'barreado', 'Alimentos preparados', '164.98', '0.24', '18.27', '9.53', '0.15', '47.63'),
    ('529', 'Bife à cavalo, com contra filé', 'bife cavalo contra file', 'Alimentos preparados', '291.23', '0.00', '23.66', '21.15', None, '82.87'),
    ('530', 'Bolinho de arroz', 'bolinho arroz', 'Alimentos preparados', '273.51', '41.68', '8.04', '8.29', '2.74', '58.86'),
    ('531', 'Camarão à baiana', 'camarao baiana', 'Alimentos preparados', '100.78', '3.17', '7.94', '5.97', '0.39', '84.79'),
    ('532', 'Charuto, de repolho', 'charuto repolho', 'Alimentos preparados', '78.23', '10.13', '6.78', '1.12', '1.46', '12.10'),
    ('533', 'Cuscuz, de milho, cozido com sal', 'cuscuz milho cozido sal', 'Alimentos preparados', '113.46', '25.28', '2.16', '0.68', '2.05', '247.67'),
    ('534', 'Cuscuz, paulista', 'cuscuz paulista', 'Alimentos preparados', '142.12', '22.51', '2.56', '4.65', '2.43', '235.71'),
    ('535', 'Cuxá, molho', 'cuxa molho', 'Alimentos preparados', '80.09', '5.74', '5.64', '3.59', '3.02', '1344.29'),
    ('536', 'Dobradinha', 'dobradinha', 'Alimentos preparados', '124.50', '0.00', '19.77', '4.44', None, '28.77'),
    ('537', 'Estrogonofe de carne', 'estrogonofe carne', 'Alimentos preparados', '173.14', '2.98', '15.03', '10.80', None, '122.85'),
    ('538', 'Estrogonofe de frango', 'estrogonofe frango', 'Alimentos preparados', '156.81', '2.59', '17.55', '7.96', None, '99.46'),
    ('539', 'Feijão tropeiro mineiro', 'feijao tropeiro mineiro', 'Alimentos preparados', '151.56', '19.58', '10.17', '6.79', '3.57', '365.07'),
    ('540', 'Feijoada', 'feijoada', 'Alimentos preparados', '116.93', '11.64', '8.67', '6.48', '5.09', '278.22'),
    ('541', 'Frango, com açafrão', 'frango acafrao', 'Alimentos preparados', '112.78', '4.06', '9.70', '6.17', '0.22', '28.81'),
    ('542', 'Macarrão, molho bolognesa', 'macarrao molho bolognesa', 'Alimentos preparados', '119.53', '22.52', '4.93', '0.89', '0.78', '8.94'),
    ('543', 'Maniçoba', 'manicoba', 'Alimentos preparados', '134.22', '3.42', '9.96', '8.70', '2.16', '406.70'),
    ('544', 'Quibebe', 'quibebe', 'Alimentos preparados', '86.35', '6.64', '8.56', '2.67', '1.67', '246.61'),
    ('545', 'Salada, de legumes, com maionese', 'salada legumes maionese', 'Alimentos preparados', '96.10', '8.92', '1.05', '7.04', '2.22', '228.43'),
    ('546', 'Salada, de legumes, cozida no vapor', 'salada legumes cozido no vapor', 'Alimentos preparados', '35.41', '7.09', '2.01', '0.31', '2.51', '2.51'),
    ('547', 'Salpicão, de frango', 'salpicao frango', 'Alimentos preparados', '147.86', '4.57', '13.93', '7.84', '0.41', '248.35'),
    ('548', 'Sarapatel', 'sarapatel', 'Alimentos preparados', '122.98', '1.09', '18.47', '4.42', None, '215.62'),
    ('549', 'Tabule', 'tabule', 'Alimentos preparados', '57.45', '10.58', '2.05', '1.21', '2.08', '1.19'),
    ('550', 'Tacacá', 'tacaca', 'Alimentos preparados', '46.89', '3.39', '6.96', '0.36', '0.21', '1349.06'),
    ('551', 'Tapioca, com manteiga', 'tapioca manteiga', 'Alimentos preparados', '347.83', '63.59', '0.09', '10.91', '0.00', '157.52'),
    ('552', 'Tucupi, com pimenta-de-cheiro', 'tucupi pimenta cheiro', 'Alimentos preparados', '27.18', '4.74', '2.06', '0.28', '0.23', '5.13'),
    ('553', 'Vaca atolada', 'vaca atolada', 'Alimentos preparados', '144.90', '10.06', '5.12', '9.32', '2.34', '25.63'),
    ('554', 'Vatapá', 'vatapa', 'Alimentos preparados', '254.89', '9.75', '6.00', '23.23', '1.70', '879.85'),
    ('555', 'Virado à paulista', 'virado paulista', 'Alimentos preparados', '306.95', '14.11', '10.18', '25.59', '2.16', '345.53'),
    ('556', 'Yakisoba', 'yakisoba', 'Alimentos preparados', '112.80', '18.25', '7.52', '2.61', '1.06', '793.76'),
    ('557', 'Amendoim, grão, cru', 'amendoim grao cru', 'Leguminosas e derivados', '544.05', '20.31', '27.19', '43.85', '8.04', '0.00'),
    ('558', 'Amendoim, torrado, salgado', 'amendoim torrado salgado', 'Leguminosas e derivados', '605.78', '18.70', '22.48', '53.96', '7.76', '375.73'),
    ('559', 'Ervilha, em vagem', 'ervilha vagem', 'Leguminosas e derivados', '88.09', '14.23', '7.45', '0.47', '9.72', '0.00'),
    ('560', 'Ervilha, enlatada, drenada', 'ervilha enlatada drenada', 'Leguminosas e derivados', '73.84', '13.44', '4.60', '0.38', '5.08', '372.11'),
    ('561', 'Feijão, carioca, cozido', 'feijao cozido', 'Leguminosas e derivados', '76.42', '13.59', '4.78', '0.54', '8.51', '1.76'),
    ('562', 'Feijão, carioca, cru', 'feijao cru', 'Leguminosas e derivados', '329.03', '61.22', '19.98', '1.26', '18.42', '0.00'),
    ('563', 'Feijão, fradinho, cozido', 'feijao fradinho cozido', 'Leguminosas e derivados', '78.01', '13.50', '5.09', '0.64', '7.47', '0.98'),
    ('564', 'Feijão, fradinho, cru', 'feijao fradinho cru', 'Leguminosas e derivados', '339.16', '61.24', '20.21', '2.37', '23.59', '10.31'),
    ('565', 'Feijão, jalo, cozido', 'feijao jalo cozido', 'Leguminosas e derivados', '92.74', '16.50', '6.14', '0.51', '13.87', '0.52'),
    ('566', 'Feijão, jalo, cru', 'feijao jalo cru', 'Leguminosas e derivados', '327.91', '61.48', '20.10', '0.95', '30.32', '24.58'),
    ('567', 'Feijão, preto, cozido', 'feijao preto cozido', 'Leguminosas e derivados', '77.03', '14.01', '4.48', '0.54', '8.40', '1.85'),
    ('568', 'Feijão, preto, cru', 'feijao preto cru', 'Leguminosas e derivados', '323.57', '58.75', '21.34', '1.24', '21.83', '0.00'),
    ('569', 'Feijão, rajado, cozido', 'feijao rajado cozido', 'Leguminosas e derivados', '84.70', '15.27', '5.54', '0.40', '9.32', '0.69'),
    ('570', 'Feijão, rajado, cru', 'feijao rajado cru', 'Leguminosas e derivados', '325.84', '62.93', '17.27', '1.17', '24.01', '13.65'),
    ('571', 'Feijão, rosinha, cozido', 'feijao rosinha cozido', 'Leguminosas e derivados', '67.87', '11.82', '4.54', '0.48', '4.76', '2.08'),
    ('572', 'Feijão, rosinha, cru', 'feijao rosinha cru', 'Leguminosas e derivados', '336.96', '62.22', '20.92', '1.33', '20.63', '24.11'),
    ('573', 'Feijão, roxo, cozido', 'feijao roxo cozido', 'Leguminosas e derivados', '76.89', '12.91', '5.72', '0.54', '11.51', '1.46'),
    ('574', 'Feijão, roxo, cru', 'feijao roxo cru', 'Leguminosas e derivados', '331.41', '59.99', '22.17', '1.24', '33.84', '9.76'),
    ('575', 'Grão-de-bico, cru', 'grao bico cru', 'Leguminosas e derivados', '354.70', '57.88', '21.23', '5.43', '12.36', '5.19'),
    ('576', 'Guandu, cru', 'guandu cru', 'Leguminosas e derivados', '344.13', '64.00', '18.96', '2.13', '21.31', '1.62'),
    ('577', 'Lentilha, cozida', 'lentilha cozido', 'Leguminosas e derivados', '92.64', '16.30', '6.31', '0.52', '7.86', '1.18'),
    ('578', 'Lentilha, crua', 'lentilha cru', 'Leguminosas e derivados', '339.14', '62.00', '23.15', '0.77', '16.94', '0.00'),
    ('579', 'Paçoca, amendoim', 'pacoca amendoim', 'Leguminosas e derivados', '486.93', '52.38', '16.00', '26.08', '7.32', '166.84'),
    ('580', 'Pé-de-moleque, amendoim', 'pe moleque amendoim', 'Leguminosas e derivados', '503.19', '54.73', '13.16', '28.05', '3.39', '16.35'),
    ('581', 'Soja, farinha', 'soja farinha', 'Leguminosas e derivados', '403.96', '38.44', '36.03', '14.63', '20.18', '5.75'),
    ('582', 'Soja, extrato solúvel, natural, fluido', 'soja extrato soluvel natural fluido', 'Leguminosas e derivados', '39.10', '4.28', '2.38', '1.61', '0.37', '56.53'),
    ('583', 'Soja, extrato solúvel, pó', 'soja extrato soluvel po', 'Leguminosas e derivados', '458.90', '28.48', '35.69', '26.18', '7.31', '83.47'),
    ('584', 'Soja, queijo (tofu)', 'soja queijo tofu', 'Leguminosas e derivados', '64.49', '2.13', '6.55', '3.95', '0.75', '1.21'),
    ('585', 'Tremoço, cru', 'tremoco cru', 'Leguminosas e derivados', '381.28', '43.79', '33.58', '10.34', '32.31', '3.29'),
    ('586', 'Tremoço, em conserva', 'tremoco conserva', 'Leguminosas e derivados', '120.64', '12.39', '11.11', '3.78', '14.44', '1808.76'),
    ('587', 'Amêndoa, torrada, salgada', 'amendoa torrada salgada', 'Nozes e sementes', '580.75', '29.55', '18.55', '47.32', '11.64', '278.52'),
    ('588', 'Castanha-de-caju, torrada, salgada', 'castanha caju torrada salgada', 'Nozes e sementes', '570.17', '29.13', '18.51', '46.28', '3.66', '125.00'),
    ('589', 'Castanha-do-Brasil, crua', 'castanha brasil cru', 'Nozes e sementes', '642.96', '15.08', '14.54', '63.46', '7.93', '0.65'),
    ('590', 'Coco, cru', 'coco cru', 'Nozes e sementes', '406.49', '10.40', '3.69', '41.98', '5.38', '15.32'),
    ('591', 'Coco, verde, cru', 'coco verde cru', 'Nozes e sementes', None, None, None, None, None, None),
    ('592', 'Farinha, de mesocarpo de babaçu, crua', 'farinha mesocarpo babacu cru', 'Nozes e sementes', '328.77', '79.17', '1.41', '0.20', '17.86', '12.46'),
    ('593', 'Gergelim, semente', 'gergelim semente', 'Nozes e sementes', '583.55', '21.62', '21.16', '50.43', '11.87', '2.57'),
    ('594', 'Linhaça, semente', 'linhaca semente', 'Nozes e sementes', '495.10', '43.31', '14.08', '32.25', '33.50', '8.67'),
    ('595', 'Pinhão, cozido', 'pinhao cozido', 'Nozes e sementes', '174.37', '43.92', '2.98', '0.75', '15.60', '0.86'),
    ('596', 'Pupunha, cozida', 'pupunha cozido', 'Nozes e sementes', '218.53', '29.57', '2.52', '12.76', '4.25', '0.91'),
    ('597', 'Noz, crua', 'noz cru', 'Nozes e sementes', '620.06', '18.36', '13.97', '59.36', '7.25', '4.57'),
)
# <<< DADOS GERADOS <<<

# Ordem das colunas no INSERT (e na comparação, sem `source`).
WRITE_COLUMNS: tuple[str, ...] = (
    "source",
    "source_id",
    "source_version",
    "name",
    "name_normalized",
    "category",
    "basis",
    "energy_kcal",
    "carbohydrates_g",
    "total_sugars_g",
    "added_sugars_g",
    "protein_g",
    "total_fat_g",
    "saturated_fat_g",
    "trans_fat_g",
    "fiber_g",
    "sodium_mg",
    "license",
    "citation",
    "owner_user_id",
    "is_active",
)
COMPARED_COLUMNS: tuple[str, ...] = WRITE_COLUMNS[1:]
UPDATED_COLUMNS: tuple[str, ...] = WRITE_COLUMNS[2:]
NUMERIC_COLUMNS: frozenset[str] = frozenset(
    {
        "energy_kcal",
        "carbohydrates_g",
        "total_sugars_g",
        "added_sugars_g",
        "protein_g",
        "total_fat_g",
        "saturated_fat_g",
        "trans_fat_g",
        "fiber_g",
        "sodium_mg",
    }
)

_ROW_PLACEHOLDERS = "(" + ", ".join(["%s"] * len(WRITE_COLUMNS)) + ")"
_UPDATE_CLAUSE = ", ".join(f"{coluna} = VALUES({coluna})" for coluna in UPDATED_COLUMNS)


class FoodCatalogSeedError(Exception):
    """Estado incompatível; nenhum statement desta execução foi aplicado."""


def _fail(message: str) -> None:
    raise FoodCatalogSeedError(f"081: {message}. Nenhum statement executado.")


def _text(value) -> str:
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8")
    return "" if value is None else str(value)


def seed_records() -> list[dict]:
    """Linhas do seed com o valor de CADA coluna escrita (Decimal nos números)."""
    registros = []
    for linha in SEED_ROWS:
        dados = dict(zip(SEED_COLUMNS, linha))
        registro = {coluna: None for coluna in WRITE_COLUMNS}
        registro.update(
            source=SOURCE,
            source_id=dados["source_id"],
            source_version=SOURCE_VERSION,
            name=dados["name"],
            name_normalized=dados["name_normalized"],
            category=dados["category"],
            basis=BASIS,
            license=LICENSE,
            citation=CITATION,
            owner_user_id=None,
            is_active=1,
        )
        for coluna in NUMERIC_COLUMNS:
            if coluna in dados and dados[coluna] is not None:
                registro[coluna] = Decimal(dados[coluna])
        registros.append(registro)
    return registros


def canonical(record: dict) -> tuple:
    """Forma comparável de uma linha (do seed ou do banco): texto, número com 2
    casas, inteiro como texto, NULL como None."""
    saida = []
    for coluna in COMPARED_COLUMNS:
        valor = record.get(coluna)
        if valor is None:
            saida.append(None)
        elif coluna in NUMERIC_COLUMNS:
            saida.append(f"{Decimal(_text(valor)):.2f}")
        elif coluna in ("owner_user_id", "is_active"):
            saida.append(str(int(valor)))
        else:
            saida.append(_text(valor))
    return tuple(saida)


def checksum(canonicals) -> str:
    """sha256 das linhas canônicas, em ordem do número da TACO."""
    ordenadas = sorted(canonicals, key=lambda linha: int(linha[0]))
    texto = "\n".join(json.dumps(list(linha), ensure_ascii=False, separators=(",", ":")) for linha in ordenadas)
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()


def seed_checksum() -> str:
    return checksum(canonical(registro) for registro in seed_records())


def blocks(records: list[dict]) -> list[list[dict]]:
    return [records[i : i + BLOCK_SIZE] for i in range(0, len(records), BLOCK_SIZE)]


def upsert_statement(block: list[dict]) -> tuple[str, tuple]:
    sql = (
        f"INSERT INTO {TABLE} ({', '.join(WRITE_COLUMNS)}) VALUES "
        + ", ".join([_ROW_PLACEHOLDERS] * len(block))
        + f" ON DUPLICATE KEY UPDATE {_UPDATE_CLAUSE}"
    )
    params = tuple(registro[coluna] for registro in block for coluna in WRITE_COLUMNS)
    return sql, params


def _read_schema(db):
    engines = {
        _text(row["TABLE_NAME"])
        for row in db.query(
            "SELECT TABLE_NAME FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s",
            (TABLE,),
        )
    }
    if TABLE not in engines:
        return None, None
    columns = {
        _text(row["COLUMN_NAME"])
        for row in db.query(
            "SELECT COLUMN_NAME FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s",
            (TABLE,),
        )
    }
    unique = [
        row
        for row in db.query(
            "SELECT SEQ_IN_INDEX, COLUMN_NAME, NON_UNIQUE FROM information_schema.STATISTICS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND INDEX_NAME = %s",
            (TABLE, UNIQUE_INDEX),
        )
    ]
    return columns, unique


def _existing_ids(db) -> list[str]:
    return [_text(row["source_id"]) for row in db.query(f"SELECT source_id FROM {TABLE} WHERE source = %s", (SOURCE,))]


def _read_block(db, ids: list[str]) -> dict[str, tuple]:
    marcadores = ", ".join(["%s"] * len(ids))
    linhas = db.query(
        f"SELECT {', '.join(COMPARED_COLUMNS)} FROM {TABLE} WHERE source = %s AND source_id IN ({marcadores})",
        (SOURCE, *ids),
    )
    return {_text(linha["source_id"]): canonical(linha) for linha in linhas}


def plan(db) -> list[tuple[str, tuple | None]]:
    """Somente leitura. Devolve [(sql, params)] a executar, na ordem."""
    registros = seed_records()
    columns, unique = _read_schema(db)
    if columns is None:
        if bool(getattr(db, "dry_run", False)):
            return [upsert_statement(bloco) for bloco in blocks(registros)]
        _fail(f"tabela {TABLE} nao existe (a 080 cria)")
    faltando = [coluna for coluna in WRITE_COLUMNS if coluna not in columns]
    if faltando:
        _fail(f"{TABLE} sem as colunas {faltando}")
    cobertas = tuple(_text(r["COLUMN_NAME"]) for r in sorted(unique, key=lambda r: int(r["SEQ_IN_INDEX"])))
    if not unique or cobertas != ("source", "source_id") or int(unique[0]["NON_UNIQUE"]) != 0:
        _fail(f"{TABLE}.{UNIQUE_INDEX} ausente ou diferente de UNIQUE (source, source_id)")

    ids_do_seed = [registro["source_id"] for registro in registros]
    existentes = _existing_ids(db)
    estranhos = sorted(set(existentes) - set(ids_do_seed))
    if estranhos:
        _fail(f"linhas source='{SOURCE}' fora do seed: {estranhos[:10]}")
    if not existentes:
        logger.info("081: nenhuma linha da TACO no banco; %d bloco(s) a gravar", len(blocks(registros)))
        return [upsert_statement(bloco) for bloco in blocks(registros)]

    statements = []
    encontrados: list[tuple] = []
    for bloco in blocks(registros):
        no_banco = _read_block(db, [registro["source_id"] for registro in bloco])
        encontrados.extend(no_banco.values())
        if any(no_banco.get(registro["source_id"]) != canonical(registro) for registro in bloco):
            statements.append(upsert_statement(bloco))
    esperado = seed_checksum()
    obtido = checksum(encontrados)
    logger.info(
        "081: %d de %d linhas da TACO no banco; checksum %s (seed %s); %d bloco(s) a gravar",
        len(encontrados), len(registros), obtido[:12], esperado[:12], len(statements),
    )
    if len(encontrados) == len(registros) and obtido == esperado:
        return []
    return statements


def plan_down(db) -> list[tuple[str, tuple | None]]:
    columns, _unique = _read_schema(db)
    if columns is None or not _existing_ids(db):
        return []
    return [(f"DELETE FROM {TABLE} WHERE source = %s", (SOURCE,))]


def up(db):
    for sql, params in plan(db):
        db.execute(sql, params)


def down(db):
    for sql, params in plan_down(db):
        db.execute(sql, params)

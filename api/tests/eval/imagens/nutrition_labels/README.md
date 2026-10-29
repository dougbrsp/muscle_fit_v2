# Imagens do eval da foto do rótulo (nutrição L6)

Usadas por `api/tests/eval/test_nutrition_label_eval.py` (eval pago, fora do CI),
com os casos e os valores esperados em `api/tests/eval/casos/nutrition_label.json`.

## Origem e licença

- **Sintéticas**, geradas por `api/tests/eval/gerar_imagens_rotulo.py` a partir do
  campo `desenho` de cada caso. Nenhuma foto de rótulo real foi baixada: não há
  banco de fotos de rótulos brasileiros com licença clara para redistribuir aqui.
- Tabelas no **modelo** da RDC nº 429/2020 + IN nº 75/2020 da Anvisa (por 100 g ou
  100 ml, por porção e %VD) e uma no modelo antigo da RDC nº 360/2003 (só por porção,
  kcal = kJ). **Produtos, marcas, números e código de barras são fictícios.**
- Fonte: **Inter** (SIL Open Font License 1.1), a mesma de `api/app/assets/fonts/inter/`.
- A "foto" simula um celular: papel com textura, rotação, perspectiva, luz desigual,
  ruído e desfoque. `biscoito_borrado.jpg` é ilegível de propósito.
- Cada arquivo tem no máximo 300 KB (plano de nutrição §9). A foto de ~1,5 MB do caso
  `biscoito_foto_grande` é ampliada a partir de `biscoito_rdc429.jpg` na hora do eval
  e não é versionada.

## Arquivos (SHA-256)

O granulado do Pillow não aceita semente, então regenerar muda os bytes: as imagens
abaixo são a referência das rodadas registradas no mapa dos motores (§56).

| Arquivo | Caso | Bytes | SHA-256 |
|---|---|---|---|
| `biscoito_rdc429.jpg` | tabela RDC 429 por 100 g, foto limpa | 105651 | `d2245315ef29a2c634c4b5b1b5715f4831bd3422b1c6e507ea7db4e0ab4abf0e` |
| `suco_uva_ml.jpg` | por 100 ml (bebida) | 106996 | `7a11c892223d4deb8a544cc8c66f349c501c75e182897d6b3e3fc2e2b7c0e88d` |
| `iogurte_foto_inclinada.jpg` | rotação, perspectiva e desfoque maiores | 87343 | `374200718a06321a9ed7517722bb5da578c6b5a4736c53bac6f146786f6eca22` |
| `granola_modelo_antigo.jpg` | RDC 360: só por porção, kcal = kJ | 93360 | `23a2362708f0385510fed164ef796314cc770db258776ba2a6b6dd6a9065e2d3` |
| `arroz_nao_contem.jpg` | "não contém quantidades significativas" | 98008 | `a02b740ce2a16cefab27239b930d41cd6bb73bc4718ea4de8fc4ffcea6c8f980` |
| `queijo_produto_codigo.jpg` | nome, marca e código de barras na foto | 121292 | `cbcf3cca5db4f8365323d93b363580c2d3489bea417893102717ca7e8d91081f` |
| `biscoito_borrado.jpg` | ilegível (desfoque forte) | 24174 | `9bf1015a6a0b7c8e271ff39e6ca12b36b0094324b98bcfa78025964382233373` |

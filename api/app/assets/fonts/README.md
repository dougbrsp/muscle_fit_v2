# Fontes dos cards ilustrados de exercícios

Usadas por `app/exercise_card_composer.py` para escrever **na aplicação** o
texto do card (Estratégia B: a IA gera só a ilustração; título, rótulos,
músculos e logo são compostos pelo backend). Decisão do usuário em 2026-10-03:
**Oswald** no título e nos rótulos, **Inter** no texto dos músculos.

Ambas sob a **SIL Open Font License 1.1** (`OFL.txt` de cada família, ao lado
dos arquivos): uso, embutir e redistribuir junto com software são permitidos;
as fontes não podem ser vendidas sozinhas e os nomes reservados não podem ser
usados em versões modificadas (aqui nada é modificado).

Arquivos **TTF estáticos** (um peso por arquivo, sem eixo variável — o peso
não depende de `set_variation_by_*`), sem alteração nenhuma em relação à
origem.

| Arquivo | Uso no card | Versão (tabela `name`) | Origem | SHA-256 |
|---|---|---|---|---|
| `oswald/Oswald-Medium.ttf` | rótulos "Posição inicial" / "Movimento" | 4.103 | [googlefonts/OswaldFont@8979526](https://github.com/googlefonts/OswaldFont/blob/89795261ac9eeb9aa8cd99f43982c4e4b0e53261/fonts/ttf/Oswald-Medium.ttf) | `d8b3ac8668c6ddbcecd299567e2130e2b933ad8b6c98b5b8ac6ff2bee9c0848a` |
| `oswald/Oswald-SemiBold.ttf` | título do rodapé "MÚSCULOS" | 4.103 | [googlefonts/OswaldFont@8979526](https://github.com/googlefonts/OswaldFont/blob/89795261ac9eeb9aa8cd99f43982c4e4b0e53261/fonts/ttf/Oswald-SemiBold.ttf) | `8c5e61737c100d0a1ca87a9a1608afc53246b8a77f8d73c56b03406adc2d9282` |
| `oswald/Oswald-Bold.ttf` | título do card, números dos selos | 4.103 | [googlefonts/OswaldFont@8979526](https://github.com/googlefonts/OswaldFont/blob/89795261ac9eeb9aa8cd99f43982c4e4b0e53261/fonts/ttf/Oswald-Bold.ttf) | `eb7d46f856dd57f18a8c03d033c57802692bf127f01dbd95ba8984338e6b5135` |
| `oswald/OFL.txt` | licença | — | [googlefonts/OswaldFont@8979526 `OFL.txt`](https://github.com/googlefonts/OswaldFont/blob/89795261ac9eeb9aa8cd99f43982c4e4b0e53261/OFL.txt) (fim de linha normalizado para LF) | `0687c51a3126ae6963389181135a2eaea02f2e88f31230d27409952f28e84da5` |
| `inter/Inter-Regular.ttf` | nomes dos músculos | 4.001 | [rsms/inter v4.1](https://github.com/rsms/inter/releases/tag/v4.1), `Inter-4.1.zip` → `extras/ttf/Inter-Regular.ttf` | `40d692fce188e4471e2b3cba937be967878f631ad3ebbbdcd587687c7ebe0c82` |
| `inter/Inter-SemiBold.ttf` | "Principais:" / "Secundários:" | 4.001 | [rsms/inter v4.1](https://github.com/rsms/inter/releases/tag/v4.1), `Inter-4.1.zip` → `extras/ttf/Inter-SemiBold.ttf` | `78a843fade9d4612a5567302fb595b56976eb5fcebf4fea5a5912d638bafcde3` |
| `inter/OFL.txt` | licença | — | [rsms/inter v4.1](https://github.com/rsms/inter/releases/tag/v4.1), `Inter-4.1.zip` → `LICENSE.txt` | `262481e844521b326f5ecd053e59b98c8b2da78c8ee1bdbb6e8174305e54935a` |

`Inter-4.1.zip` (release oficial do autor) tem SHA-256
`9883fdd4a49d4fb66bd8177ba6625ef9a64aa45899767dde3d36aa425756b11e`. O
repositório `google/fonts` só distribui o Inter e o Oswald como fonte
variável; os estáticos vêm dos repositórios oficiais de cada família.

## Garantias (testes)

- `api/tests/unit/test_card_fonts_asset.py`: cada TTF carrega com
  `ImageFont.truetype`, tem a família e o peso esperados, e renderiza
  `ÁÂÃÀÇÉÊÍÓÔÕÚáâãàçéêíóôõú` sem cair no glifo `.notdef` (a máscara de cada
  caractere é comparada com a de um code point que a fonte não tem); o
  SHA-256 de cada arquivo confere com esta tabela.
- `.github/scripts/smoke-lambda-image.sh` (CI das imagens Docker): as fontes
  carregam e renderizam os acentos **dentro** das imagens da `musclefit-api-core`
  (`Dockerfile`) e do worker `musclefit-ai-generators` (`Dockerfile.ai`, onde o
  card é composto logo após a OpenAI).

## Onde entra em produção

O diretório fica dentro de `api/app/`, que todas as imagens Lambda copiam
(`COPY app ...`) e que o allowlist de cada `Dockerfile.*.dockerignore` libera
com `!app`. Nada no `.dockerignore` exclui `.ttf`/`.txt`/`.md` dentro de
`app/`. Custo: ~1,2 MB a mais em cada imagem.

## Como trocar ou atualizar

Substitua o arquivo pelo novo TTF **estático** da origem oficial, atualize a
versão, a URL (com o commit/tag) e o SHA-256 nesta tabela, e rode
`cd api && python -m pytest tests/unit/test_card_fonts_asset.py tests/unit/test_exercise_card_composer.py`.
Mudou a aparência do card? Suba `LAYOUT_VERSION` em
`app/exercise_card_composer.py`.

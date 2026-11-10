# Logo oficial para composição dos cards de exercícios

`musclefit-logo.png` é o logo oficial do MuscleFit (lockup vertical: símbolo +
wordmark "MUSCLE FIT" + tagline), usado pelo backend para **aplicar o logo
programaticamente** sobre o card ilustrado de exercício: o compositor
(`app/exercise_card_composer.py`) cola o logo por último, no worker, logo
depois da OpenAI — o preview que o admin revisa já tem o logo, e a aprovação só
publica, sem recompor. A IA que gera a ilustração é instruída a **nunca**
desenhar logo, nome de marca ou qualquer texto — o logo real entra sempre por
esta imagem.

| Propriedade | Valor |
|---|---|
| Origem | `frontend/public/logo.svg` (viewBox 902 × 746, fundo transparente, só paths — sem dependência de fonte) |
| Formato | PNG RGBA, fundo transparente |
| Tamanho | 1200 × 992 px (largura fixa, proporção do viewBox preservada) |
| Rasterizador | `@resvg/resvg-js` — o mesmo dos ícones PWA (`frontend/scripts/generate-pwa-icons.mjs`) |

O backend só **reduz** este raster (o logo ocupa cerca de 22 % da largura do
card), nunca o amplia: 1200 px cobrem cards de até ~5000 px de largura.

## Como regenerar

Sempre que `frontend/public/logo.svg` mudar:

```bash
cd frontend
npm ci        # se ainda não houver node_modules
node scripts/generate-brand-logo-png.mjs
```

O script sobrescreve `api/app/assets/branding/musclefit-logo.png`; versione o
PNG no mesmo PR da mudança do SVG. `api/tests/unit/test_branding_logo_asset.py`
reprova se o PNG deixar de ser RGBA, ficar estreito demais (< 1024 px) ou se a
proporção divergir do viewBox do SVG em mais de 0,5 % (sinal de PNG
desatualizado em relação ao logo).

## Onde entra em produção

O diretório fica dentro de `api/app/`, que todas as imagens Lambda copiam
(`COPY app ...`) e que o allowlist de `api/Dockerfile.dockerignore` (e dos
demais `Dockerfile.*.dockerignore`) já libera com `!app`. O smoke de CI
(`.github/scripts/smoke-lambda-image.sh`) confere que o PNG está nas imagens
da `musclefit-api-core` (aprovação) e do worker `musclefit-ai-generators`
(`Dockerfile.ai`, onde o card é composto), junto com as fontes de
`app/assets/fonts/`, e que o Pillow da imagem grava WebP.

## Logo para estampa (`musclefit-logo-estampa.png`)

Derivado de `musclefit-logo.png` **sem o slogan** "AI POWERED WORKOUT" (decisao
do usuario: versao `sem-slogan`, simbolo MF + silhueta + "MUSCLE FIT"). Nao e o
logo colado pelo compositor: o worker de cards V3 o anexa como a **ultima
imagem de entrada** das duas cenas da OpenAI, para o atleta gerado vestir roupa
com o logo no peito (o texto do prompt diz "the LAST attached image").

| Propriedade | Valor |
|---|---|
| Recorte | caixa `(0, 0, 1200, 925)` do PNG oficial (1200 x 992): o nome termina na linha 900 e o slogan comeca na 946 |
| Achatamento | sobre cinza neutro `(128, 128, 128)`, RGB, sem alfa |
| Tamanho | 1024 x 789 px (lado maior = 1024, proporcao 1200:925 preservada) |

O asset ja e versionado achatado; `exercise_card_processor.load_stamp_logo_png()`
(cache em modulo) valida e achata de novo se algum dia o arquivo vier com alfa.
Se o arquivo faltar, o job falha cedo (`CARD_COMPOSITION_FAILED`) antes de
qualquer chamada paga. Para regenerar a partir do logo oficial (da raiz do repo):

```python
from PIL import Image

base = "api/app/assets/branding/"
logo = Image.open(base + "musclefit-logo.png").convert("RGBA").crop((0, 0, 1200, 925))
fundo = Image.new("RGBA", logo.size, (128, 128, 128, 255))
fundo.alpha_composite(logo)
saida = fundo.convert("RGB")
saida.thumbnail((1024, 1024), Image.LANCZOS)
saida.save(base + "musclefit-logo-estampa.png", optimize=True)
```

Se `musclefit-logo.png` for regenerado com outra arte, refaca o recorte (o
teste `test_branding_logo_asset.py` confere a proporcao e que nao sobra slogan).

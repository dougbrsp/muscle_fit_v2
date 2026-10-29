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

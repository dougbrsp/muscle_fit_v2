# Mapa corporal v1 (frente + costas)

Arte e regiões musculares usadas pelo backend para destacar músculos de forma
**determinística** (só Pillow, `app/body_map_render.py`):

- rodapé do card ilustrado de exercício (`app/exercise_card_composer.py`,
  `CARD_LAYOUT_V2`): principais em laranja forte com brilho, secundários em
  tom fraco;
- `GET /workout-plans/{id}/muscle-map.png` (`app/muscle_map.py`).

A web usa a **mesma** geometria: `regions.json` é versionado byte a byte em
`frontend/public/bodymap/v1/regions.json` (um teste compara os dois quando o
arquivo do frontend existe no checkout) e a fórmula de cor é a mesma (SVG
`feColorMatrix` sobre a arte recortada pelo path).

## Aprovação

Protótipo "caminho 3" aprovado pelo usuário em **2026-10-03**, com todas as
fronteiras como estão (substitui os polígonos do `react-body-highlighter` em
todo o app). Decisões anatômicas aceitas junto: serrátil em `obliques`;
infraespinal e redondo em `upper-back`; adutor frontal como faixa fina; vasto
lateral visto de costas sem destaque; `knees`, cabeça, mãos e pés sem
destaque.

## Arquivos

| Arquivo | O que é |
|---|---|
| `bodymap-base.webp` | arte grafite RGBA 1536x1024 (frente à esquerda, costas à direita), fundo transparente, WebP q90 (233 KB) |
| `regions.json` | **fonte canônica**: um path SVG por slug (viewBox 0 0 1536 1024, `evenodd`); 16 slugs do banco + `neck` + o alias `abductors` |
| `masks/<slug>.png` | máscara 1 bit por path de `regions.json` (rasterizada dos paths, supersampling 4x; 0,5–1,9 KB cada) |
| `manifest.json` | caixas das vistas (silhueta ± 12 px), `lum_lo/lum_hi` (p5/p95 da luminância do corpo), `K0/K1/dim`, aliases, origem e sha256/bytes de cada arquivo |

Total ≈ 330 KB por imagem Docker. O PNG mestre (2,5 MB) **não** é versionado:
fica na pasta do protótipo, fora do git
(`api/scripts/calibration-out/bodymap-20261003-211252/base_v2_high.png` na
máquina de desenvolvimento; sha256 em `manifest.json` → `master`).

## Fórmula

```
L      = luminância da arte (modo "L" do Pillow: .299 R + .587 G + .114 B)
n      = clamp((L - lum_lo) / (lum_hi - lum_lo))
tinta  = cor * (K0 + K1 * n)                     K0 = 0,30  K1 = 0,70
fundo  = arte * dim                              dim = 0,82
saída  = mix(fundo, tinta, máscara suavizada * strength)
brilho = (principal) máscara desfocada (raio 18) em "screen" com a cor a 50 %;
         alfa = max(alfa da arte, 0,7 * brilho)
```

## Como regenerar

Pipeline em `tools/bodymap/` (README de lá: passo a passo, custo ~US$ 0,35 de
OpenAI + rotulagem manual). Trocar a arte exige refazer a segmentação e a
rotulagem; publique numa pasta nova (`v2/`), suba a versão do manifest e o
`LAYOUT_VERSION` do card. As imagens Docker recebem esta pasta pelo
`COPY app` (o allowlist `!app` dos `Dockerfile.*.dockerignore` libera tudo em
`app/`); o smoke de CI (`.github/scripts/smoke-lambda-image.sh`) exige os
arquivos e um render de teste nas imagens `Dockerfile` e `Dockerfile.ai`.

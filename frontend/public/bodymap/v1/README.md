# Mapa corporal v1

Arte e regiões do `MuscleBodyMap` (Modo Treino e recovery). O pacote foi aprovado pelo usuário em **2026-10-03**. Ele substituiu o `react-body-highlighter`.

| Arquivo | O que é |
|---|---|
| `bodymap-base.webp` | arte base 1536x1024 (frente e costas, fundo transparente), 233 KB |
| `bodymap-base@768.webp` | a mesma arte em 768x512 (72 KB), usada em tela de densidade 1 |
| `regions.json` | **fonte canônica das regiões**: um path SVG por slug, viewBox `0 0 1536 1024`, `fill-rule: evenodd`. O backend versiona o MESMO arquivo, e os testes comparam o sha256 |
| `manifest.json` | caixas das vistas frente/costas, aliases (`abductors` -> `adductor`), parâmetros do compositor (`lum_lo/hi`, `K0/K1`, `dim`) e sha256 de cada arquivo |

Os arquivos são cópias byte a byte da pasta `deliverable/` do protótipo (`api/scripts/calibration-out/bodymap-20261003-211252/`, fora do git). Na escolha entre os três caminhos testados, o aprovado foi o caminho 3: segmentação numa edição + snap nos sulcos + potrace.

## Como o frontend usa

- `src/components/bodymap/regions.v1.ts` é **gerado** a partir de `regions.json` + `manifest.json` por `node scripts/generate-bodymap-regions.mjs` (não edite à mão). O teste `scripts/generate-bodymap-regions.test.mjs` falha se o `.ts` ficar fora de sincronia ou se algum asset mudar de bytes.
- Cada região destacada é a própria arte recortada pelo path (`clipPath`) e tingida por `feColorMatrix` com a fórmula do compositor do backend (Pillow): `tinta = cor × (K0 + K1 × n)`. Assim o web e o PNG do backend ficam iguais.

## Como regenerar

Pelo pipeline do protótipo (arte base com `gpt-image-1.5`, segmentação, rotulagem humana, watershed e potrace). Ele vai ser versionado em `tools/bodymap/` no PR do backend. Uma arte nova precisa refazer a segmentação e a rotulagem. Ela entra como **v2** em outra pasta (`public/bodymap/v2/`), para que um navegador com a arte antiga em cache nunca a combine com regiões novas. Depois de trocar os arquivos, rode o gerador e atualize o sha256 no backend.

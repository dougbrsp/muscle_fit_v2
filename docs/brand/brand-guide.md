# MuscleFit — identidade visual (referência para a Fase 4)

> Logo fornecido pelo usuário em 2026-09-04, salvo em `docs/brand/logo.jpg`. Base para a nova identidade visual do frontend AWS (o frontend local não muda — ver `README.md`).

## Elementos do logo

- Símbolo: braço flexionado (bíceps) em silhueta, sobreposto às letras "MF", dentro de um arco/círculo aberto.
- Wordmark: "MUSCLE FIT" em caixa alta, tipografia condensada, itálica/inclinada, estilo esportivo/impact.
- Tagline: "AI POWERED WORKOUT", caixa alta, tracking largo, peso mais leve que o wordmark.

## Paleta extraída (aproximada — ajustar com o usuário/design antes de fixar como token final)

| Uso | Cor aproximada | Hex aproximado |
|---|---|---|
| Fundo | Preto | `#0A0A0A` |
| Gradiente primário (símbolo, "MUSCLE") | Laranja → vermelho-alaranjado | `#FF9500` → `#E8451C` |
| Contraste/secundário (parte do "F", "FIT") | Branco/prata | `#F5F5F5` |
| Texto de apoio (tagline) | Branco levemente acinzentado | `#E0E0E0` |

## Tipografia (impressão visual, não confirmada como fonte exata)

- Wordmark: fonte condensada, bold, itálica — família no estilo "Anton", "Oswald Bold Italic" ou "Bebas Neue" com itálico sintético.
- Tagline: mesma família ou uma sans-serif condensada mais leve, tracking largo.

## Decisões confirmadas (2026-09-04)

- **Tema escuro é o padrão do app** — não é só cor de acento sobre fundo claro, o fundo preto do logo vira o fundo padrão da interface.
- **Só existe o arquivo JPG** (`docs/brand/logo.jpg`) — sem vetorial (SVG/AI), sem variação com fundo transparente ou versão para tema claro. Implicações para a Fase 4:
  - Como o fundo do JPG já é preto e o app vai usar tema escuro por padrão, o logo pode ser usado diretamente sem precisar de versão transparente na maioria dos lugares (header sobre fundo escuro, tela de login) — o retângulo preto do JPG se funde com o fundo do app.
  - Onde precisar do logo sobre uma superfície diferente (ex.: um card claro, um favicon, um app icon), vai ser necessário recriar/extrair uma versão isolada (recorte do símbolo "MF" sem o fundo preto) — não é possível fazer isso de forma confiável a partir de um JPG com fundo preto sólido sem risco de bordas serrilhadas; se chegar a esse ponto, sinalizar que o ideal é pedir ao usuário uma versão vetorial ou com fundo transparente.

## Aplicação na Fase 4

Isso deve orientar:
- Paleta de cores do design system do frontend, com **tema escuro como padrão** (fundo `#0A0A0A`-like, acentos em gradiente laranja/vermelho, contraste branco).
- Logo/wordmark (JPG, fundo preto) no header e telas de autenticação, aproveitando o fundo escuro do próprio app.
- Tom visual geral: esportivo, alto contraste, "power/intensidade" — coerente com a proposta "AI Powered Workout".
